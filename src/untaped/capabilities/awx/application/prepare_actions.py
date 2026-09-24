"""Validate a complete action selection and freeze inventory source expansion."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from untaped.capabilities.awx.application.ports import Catalog, ResourceClient
from untaped.capabilities.awx.application.selection import (
    SelectedResource,
    SelectionRequest,
    SelectionResolver,
)
from untaped.capabilities.awx.domain import ResourceSpec
from untaped.capabilities.awx.errors import LaunchPromptError
from untaped.capability_api import ConfigError, UsageError, q

# Launch payload field → (template prompt flag, CLI flag that sets it).
LAUNCH_PROMPTS: dict[str, tuple[str, str]] = {
    "extra_vars": ("ask_variables_on_launch", "--extra-vars"),
    "limit": ("ask_limit_on_launch", "--host-pattern"),
    "inventory": ("ask_inventory_on_launch", "--inventory"),
    "credentials": ("ask_credential_on_launch", "--credential"),
    "scm_branch": ("ask_scm_branch_on_launch", "--scm-branch"),
    "job_tags": ("ask_tags_on_launch", "--job-tag"),
    "skip_tags": ("ask_skip_tags_on_launch", "--skip-tag"),
    "verbosity": ("ask_verbosity_on_launch", "--verbosity"),
    "diff_mode": ("ask_diff_mode_on_launch", "--diff-mode"),
    "job_type": ("ask_job_type_on_launch", "--job-type"),
}


def _preflight_launch(
    client: ResourceClient,
    spec: ResourceSpec,
    item: SelectedResource,
    payload: Mapping[str, Any],
) -> None:
    info = client.sub_endpoint_request(spec, item.id, "launch", "GET")
    label = f"{spec.kind} {item.name!r} (id={item.id})"
    needed = info.get("variables_needed_to_start") or []
    supplied = _extra_var_names(payload.get("extra_vars"))
    missing = [name for name in needed if name not in supplied]
    if missing:
        raise LaunchPromptError(
            f"{label} requires survey variables {', '.join(map(str, missing))}; "
            "pass them with --extra-vars KEY=VAL"
        )
    for field, value in payload.items():
        prompt = LAUNCH_PROMPTS.get(field)
        if prompt is None:
            continue
        ask_key, flag = prompt
        if info.get(ask_key) is not False:
            continue
        if field == "extra_vars":
            names = _extra_var_names(value)
            if not names:
                continue
            if info.get("survey_enabled"):
                _check_survey_variables(client, spec, item, label, names)
                continue
        elif _is_template_value(field, value, _template_value(field, info, item.record)):
            # AWX treats a value equal to the template's own as a no-op.
            continue
        raise LaunchPromptError(
            f"{label} does not prompt for {field} on launch ({ask_key} is false); "
            f"AWX would ignore {flag}; enable {ask_key} on the template or drop {flag}"
        )


def _check_survey_variables(
    client: ResourceClient,
    spec: ResourceSpec,
    item: SelectedResource,
    label: str,
    names: set[str],
) -> None:
    """Without ``ask_variables_on_launch`` AWX keeps only the survey's variables."""
    survey = client.sub_endpoint_request(spec, item.id, "survey_spec", "GET")
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


def _extra_var_names(value: Any) -> set[str]:
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
                f"sync is unsupported for {item.record.get('kind')}"
            )
    by_parent = _sources_by_parent(
        client, catalog, source_spec, parent_field, [item.id for item in selected]
    )
    expanded: dict[int, SelectedResource] = {}
    for item in selected:
        sources = by_parent.get(item.id)
        if not sources:
            raise ConfigError(
                f"{spec.kind} {item.name!r} (id={item.id}): no inventory sources to sync"
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
            _preflight_launch(client, spec, item, payload or {})
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
            raise ConfigError(f"inventory source {q(item.name)} (id={item.id}): no syncable source")
        if spec.kind == "Project" and not item.record.get("scm_type"):
            raise ConfigError(
                f"project {q(item.name)} (id={item.id}): a manual project cannot sync"
            )
    return spec, targets
