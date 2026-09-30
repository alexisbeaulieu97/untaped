"""Validate a complete action selection, freeze inventory source expansion, preview launches."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from itertools import pairwise
from typing import Any

import yaml

from untaped.capabilities.awx.application.mutation_values import REDACTED
from untaped.capabilities.awx.application.ports import Catalog, ResourceClient
from untaped.capabilities.awx.application.selection import (
    SelectedResource,
    SelectionRequest,
    SelectionResolver,
)
from untaped.capabilities.awx.domain import ResourceSpec
from untaped.capabilities.awx.domain.launch_prompts import PROMPT_FLAGS
from untaped.capabilities.awx.errors import LaunchPromptError
from untaped.capability_api import ConfigError, UntapedError, UsageError, q

_CLI_FLAGS: dict[str, str] = {
    "extra_vars": "--extra-vars",
    "limit": "--host-pattern",
    "inventory": "--launch-inventory",
    "credentials": "--credential",
    "scm_branch": "--scm-branch",
    "job_tags": "--job-tag",
    "skip_tags": "--skip-tag",
    "verbosity": "--verbosity",
    "diff_mode": "--diff-mode",
    "job_type": "--job-type",
}
# Launch payload field → (template prompt flag, CLI flag that sets it).
LAUNCH_PROMPTS: dict[str, tuple[str, str]] = {
    field: (PROMPT_FLAGS[field], flag) for field, flag in _CLI_FLAGS.items()
}


def preflight_launch(
    client: ResourceClient,
    spec: ResourceSpec,
    item: SelectedResource,
    payload: Mapping[str, Any],
    *,
    catalog: Catalog,
    read: Callable[[str], Mapping[str, Any]] | None = None,
    name_fields: bool = False,
) -> dict[str, Any]:
    """Raise :class:`LaunchPromptError` when AWX would ignore a field or lacks a survey var.

    ``read`` GETs one of the template's sub-endpoints (``launch``, ``survey_spec``),
    so a caller checking many payloads can cache them. Messages name the
    ``launch`` CLI flags, or with ``name_fields`` the payload fields.

    A field the template does not prompt for is a no-op, not ignored, when
    the template has its value already: its own value, the branch of its
    project when it names none, or (with no survey) extra vars it saves with
    those values. Returns ``payload`` without them, the launch AWX would run.
    """
    if read is None:

        def read(endpoint: str) -> Mapping[str, Any]:
            return client.sub_endpoint_request(spec, item.id, endpoint, "GET")

    info = read("launch")
    label = f"{spec.kind} {item.name!r} (id={item.id})"
    needed = info.get("variables_needed_to_start") or []
    supplied = extra_var_names(payload.get("extra_vars"))
    missing = [name for name in needed if name not in supplied]
    if missing:
        raise LaunchPromptError(
            f"{label} requires survey variables {', '.join(map(str, missing))}; "
            + ("set them in extra_vars" if name_fields else "pass them with --extra-vars KEY=VAL")
        )
    launch = dict(payload)
    for field, value in payload.items():
        prompt = LAUNCH_PROMPTS.get(field)
        if prompt is None:
            continue
        ask_key, flag = prompt[0], field if name_fields else prompt[1]
        if info.get(ask_key) is not False:
            continue
        current = _template_value(field, info, item.record)
        if field == "extra_vars":
            variables = _variables(value)
            if not variables:
                continue
            saved = _variables(current)
            names = {
                name for name, var in variables.items() if name not in saved or saved[name] != var
            }
            if info.get("survey_enabled"):
                _check_survey_variables(read, label, names)
                continue
            if not names:
                # AWX drops the variables the template saves with these values.
                del launch[field]
                continue
        elif _is_template_value(field, value, current) or (
            field == "scm_branch" and _is_project_branch(client, catalog, item, value, current)
        ):
            # AWX treats a value equal to the template's own as a no-op.
            del launch[field]
            continue
        raise LaunchPromptError(
            f"{label} does not prompt for {field} on launch ({ask_key} is false); "
            f"AWX would ignore {flag}; enable {ask_key} on the template or drop {flag}",
            details={"field": field},
        )
    return launch


def _variables(value: Any) -> dict[str, Any]:
    """An ``extra_vars`` value (a mapping, or its YAML or JSON text) as a mapping."""
    if isinstance(value, str):
        try:
            value = yaml.safe_load(value)
        except yaml.YAMLError:
            return {}
    return value if isinstance(value, dict) else {}


def _is_project_branch(
    client: ResourceClient, catalog: Catalog, item: SelectedResource, branch: Any, current: Any
) -> bool:
    """Whether a job template naming no branch (``current``) runs ``branch``, its project's.

    ``False`` when the template has no project, or it cannot be read.
    """
    project = item.record.get("project")
    if current != "" or not isinstance(project, int) or isinstance(project, bool):
        return False
    try:
        return bool(client.get(catalog.get("Project"), project).get("scm_branch") == branch)
    except UntapedError:
        return False


def _check_survey_variables(
    read: Callable[[str], Mapping[str, Any]],
    label: str,
    names: set[str],
) -> None:
    """Without ``ask_variables_on_launch`` AWX keeps only the survey's variables."""
    survey = read("survey_spec")
    questions = survey.get("spec") if isinstance(survey, Mapping) else None
    allowed = {
        str(question["variable"])
        for question in questions or []
        if isinstance(question, Mapping) and "variable" in question
    }
    if outside := sorted(names - allowed):
        raise LaunchPromptError(
            f"{label} does not prompt for extra variables outside its survey "
            f"(ask_variables_on_launch is false); AWX would ignore {', '.join(outside)}; "
            "enable ask_variables_on_launch or pass only survey variables"
        )


def _template_value(field: str, info: Mapping[str, Any], record: Mapping[str, Any]) -> Any:
    """The template's current value for ``field`` (launch ``defaults`` first)."""
    defaults = info.get("defaults")
    if isinstance(defaults, Mapping) and field in defaults:
        return defaults[field]
    if field == "credentials":
        return (record.get("summary_fields") or {}).get("credentials")
    return record.get(field)


def _is_template_value(field: str, supplied: Any, current: Any) -> bool:
    if field == "credentials":
        # Supplying credentials the template already has adds nothing.
        current_ids = {c.get("id") if isinstance(c, Mapping) else c for c in current or []}
        return isinstance(supplied, list) and set(supplied) <= current_ids
    if isinstance(current, Mapping):
        current = current.get("id")
    return bool(supplied == current)


_SECRET_WORDS = frozenset({"pass", "passwd", "password", "passphrase", "pwd", "secret", "token"})
_SECRET_PAIRS = frozenset({"apikey", "accesskey", "privatekey", "secretkey", "sshkey"})
_WORD = re.compile(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])")


def looks_secret(name: str) -> bool:
    """Whether a variable name reads as a secret, in any case or separator style.

    Its words (split on ``_``, ``-``, ``.`` and camelCase) are checked:
    ``pass``/``password``/``passphrase``/``pwd``/``secret``/``token``, any
    word ending in ``password``/``passphrase``/``secret``/``token``
    (``dbpassword``), or two adjacent words forming ``api_key``,
    ``access_key``, ``private_key``, ``secret_key`` or ``ssh_key`` (also
    written as one word).
    """
    words = [word.lower() for word in _WORD.findall(name)]
    candidates = [*words, *(a + b for a, b in pairwise(words))]
    return any(
        word in _SECRET_WORDS
        or word in _SECRET_PAIRS
        or word.endswith(("password", "passphrase", "secret", "token"))
        for word in candidates
    )


class TemplateReads:
    """Memoized GETs of each target template's ``launch/`` and ``survey_spec/`` endpoints.

    One instance serves a launch's preflight and its ``--dry-run`` preview,
    so neither reads an endpoint the other already read.
    """

    def __init__(self, client: ResourceClient, spec: ResourceSpec) -> None:
        self._client = client
        self._spec = spec
        self._cache: dict[tuple[int, str], Mapping[str, Any]] = {}

    def for_item(self, item: SelectedResource) -> Callable[[str], Mapping[str, Any]]:
        """The cached reader of ``item``'s sub-endpoints."""

        def read(endpoint: str) -> Mapping[str, Any]:
            key = (item.id, endpoint)
            if key not in self._cache:
                self._cache[key] = self._client.sub_endpoint_request(
                    self._spec, item.id, endpoint, "GET"
                )
            return self._cache[key]

        return read


def launch_payload_preview(
    payload: Mapping[str, Any], read: Callable[[str], Mapping[str, Any]]
) -> dict[str, Any]:
    """The launch payload as submitted, ``extra_vars`` decoded, with secrets redacted.

    A variable is secret when the template's enabled survey asks for it as a
    ``password`` (``read`` GETs the template's sub-endpoints), or when its
    name, at any depth, :func:`looks_secret`.
    """
    preview = dict(payload)
    extra_vars = preview.get("extra_vars")
    if isinstance(extra_vars, str):
        extra_vars = json.loads(extra_vars)
    if isinstance(extra_vars, dict):
        passwords = _survey_passwords(read)
        preview["extra_vars"] = {
            key: REDACTED if key in passwords else _redact_secret_names(key, value)
            for key, value in extra_vars.items()
        }
    return preview


def _survey_passwords(read: Callable[[str], Mapping[str, Any]]) -> set[str]:
    """Variables the template's enabled survey asks for as ``password`` questions."""
    if not read("launch").get("survey_enabled"):
        return set()
    survey = read("survey_spec")
    questions = survey.get("spec") if isinstance(survey, Mapping) else None
    return {
        str(question["variable"])
        for question in questions or []
        if isinstance(question, Mapping) and question.get("type") == "password"
    }


def _redact_secret_names(key: str, value: Any) -> Any:
    if looks_secret(key):
        return REDACTED
    if isinstance(value, dict):
        return {k: _redact_secret_names(str(k), v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_secret_names("", v) for v in value]
    return value


def extra_var_names(value: Any) -> set[str]:
    """The variable names of an ``extra_vars`` value (a mapping, or its JSON text)."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return set()
    return set(value) if isinstance(value, dict) else set()


_PARENT_IDS_PER_REQUEST = 100
"""Keeps an ``inventory__in`` query URL well inside common length limits."""


def _sources_by_parent(
    client: ResourceClient,
    catalog: Catalog,
    source_spec: ResourceSpec,
    parent_field: str,
    parent_ids: Sequence[int],
) -> dict[int, list[SelectedResource]]:
    """List every parent's sources with ``<parent>__in`` (one request per 100 parents)."""
    ids = list(dict.fromkeys(parent_ids))
    by_parent: dict[int, list[SelectedResource]] = {}
    for start in range(0, len(ids), _PARENT_IDS_PER_REQUEST):
        chunk = ids[start : start + _PARENT_IDS_PER_REQUEST]
        sources = SelectionResolver(client, catalog).resolve(
            source_spec,
            SelectionRequest(
                filters={f"{parent_field}__in": ",".join(map(str, chunk))},
                require_explicit=True,
            ),
        )
        for source in sources:
            parent = source.record.get(parent_field)
            if isinstance(parent, int) and parent in chunk:
                by_parent.setdefault(parent, []).append(source)
    return by_parent


def _expand_sources(
    client: ResourceClient,
    catalog: Catalog,
    spec: ResourceSpec,
    selected: Sequence[SelectedResource],
    source_spec: ResourceSpec,
) -> tuple[ResourceSpec, tuple[SelectedResource, ...]]:
    """Freeze each selected inventory's current sources, in selection order."""
    parent_field = source_spec.parent_field or spec.kind.lower()
    for item in selected:
        if item.record.get("kind", "") not in ("", "constructed"):
            raise ConfigError(
                f"{spec.kind} {item.name!r} (id={item.id}): "
                f"sync is unsupported for {item.record.get('kind')}",
                category="invalid",
            )
    by_parent = _sources_by_parent(
        client, catalog, source_spec, parent_field, [item.id for item in selected]
    )
    expanded: dict[int, SelectedResource] = {}
    for item in selected:
        sources = by_parent.get(item.id)
        if not sources:
            raise ConfigError(
                f"{spec.kind} {item.name!r} (id={item.id}): no inventory sources to sync",
                category="invalid",
            )
        expanded.update((source.id, source) for source in sources)
    return source_spec, tuple(expanded.values())


def prepare_action_targets(
    client: ResourceClient,
    catalog: Catalog,
    spec: ResourceSpec,
    selected: Sequence[SelectedResource],
    *,
    action: str,
    payload: Mapping[str, Any] | None = None,
    reads: TemplateReads | None = None,
) -> tuple[ResourceSpec, tuple[SelectedResource, ...]]:
    """Resolve every eligible source before any POST, never server-side aggregate sync.

    Launches are preflighted against ``GET <template>/launch/`` so a field
    the template does not prompt for (AWX silently ignores it) or a missing
    required survey variable fails the whole selection before any POST.
    """
    if not selected:
        raise UsageError(f"no {spec.kind} targets selected for {action}")
    if action == "launch":
        for item in selected:
            read = reads.for_item(item) if reads is not None else None
            preflight_launch(client, spec, item, payload or {}, catalog=catalog, read=read)
    if action != "sync":
        return spec, tuple(selected)
    targets = tuple(selected)
    action_spec = next((item for item in spec.actions if item.name == action), None)
    if action_spec is not None and action_spec.expand_to is not None:
        spec, targets = _expand_sources(
            client, catalog, spec, selected, catalog.get(action_spec.expand_to)
        )
    for item in targets:
        if spec.kind == "InventorySource" and item.record.get("source") in (None, "", "file"):
            raise ConfigError(
                f"inventory source {q(item.name)} (id={item.id}): no syncable source",
                category="invalid",
            )
        if spec.kind == "Project" and not item.record.get("scm_type"):
            raise ConfigError(
                f"project {q(item.name)} (id={item.id}): a manual project cannot sync",
                category="invalid",
            )
    return spec, targets
