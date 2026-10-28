"""A kind's actions: ``launch`` (templates) and ``sync`` (projects and inventories).

Owns the ``LAUNCH_FLAGS`` table (which launch option fills which payload
field); submission and monitoring use the shared action runner.
"""

import datetime
import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field

from untaped.sdk import (
    Confirm,
    Failed,
    OutcomeRecord,
    Output,
    Parallel,
    Positional,
    Record,
    Stdin,
    UsageError,
    read_structured_file,
)
from untaped_awx.application.resources.ports import FkResolver
from untaped_awx.application.scheduling import MAX_PARALLEL
from untaped_awx.commands._action_runner import run_action_selection
from untaped_awx.commands._awx import ResourceCommand
from untaped_awx.commands._options import (
    NAMES,
    OrganizationScope,
    Selection,
    Waiting,
    Writes,
    scope_values,
)
from untaped_awx.commands._selection import is_mass, select
from untaped_awx.commands.context import scope_for_command
from untaped_awx.errors import ResourceNotFoundError, default_organization_note
from untaped_awx.infrastructure.resources.spec import AwxResourceSpec


class _Action(ResourceCommand):
    """A launch or sync of a fixed selection, optionally waited on or followed."""

    names: Positional[list[str]] = Field(default_factory=list, description=NAMES)
    scope: OrganizationScope
    selection: Selection
    stdin: Stdin[Record]
    confirm: Confirm
    writes: Writes
    parallel: Parallel = Parallel(default=1, cap=MAX_PARALLEL)
    waiting: Waiting
    output: Output[OutcomeRecord]

    def _run(self, action: str, payload: Callable[..., dict[str, Any] | None]) -> Iterator[Any]:
        self.waiting.check()
        with self.awx() as awx:
            selected = select(
                awx, self.spec, self.names, self.selection, self.stdin, scope_values(self.scope)
            )
            rows = run_action_selection(
                awx,
                self.spec,
                selected,
                action=action,
                payload=payload(awx),
                confirm=self.confirm,
                waiting=self.waiting,
                # Mass or multi-target selections preview and confirm first.
                ask=len(selected) > 1 or is_mass(self.selection, self.stdin),
                parallel=self.parallel.value,
                fail_fast=self.writes.fail_fast,
                compact=self.output.format in ("table", "raw"),
            )
        yield from rows
        # a dry run's rows are ``planned``: nothing ran, so nothing failed
        if not self.confirm.dry_run and any(row.action != "completed" for row in rows):
            raise Failed


class Sync(_Action):
    """Sync a fixed selection; inventories expand to their current source IDs.

    Examples
    --------
    ```console
    $ untaped awx projects sync Playbooks --wait
    $ untaped awx inventory-sources sync Cloud --inventory Production --wait
    ```
    """

    def run(self) -> Iterator[OutcomeRecord]:
        return self._run("sync", lambda awx: None)


class Launch(_Action):
    """Launch one or more resources and (optionally) wait for each job.

    ## Launch values

    A later `--extra-vars` entry wins per key. YAML dates become ISO strings;
    values JSON cannot carry (`.nan`, `!!binary`) are a usage error.
    `--organization` scopes the template lookup and the `--launch-inventory` and
    `--credential` names. A flag the kind never takes is unknown (exit 2).

    Before any POST, each template's launch settings are read:

    - A flag whose `ask_*_on_launch` is false is a usage error, since AWX would
      silently ignore it. Enable the prompt on the template; `--scm-branch` also
      needs the project to allow branch override.
    - It is accepted when the template already has that value: its own, its
      project's branch for a `--scm-branch` it does not set, or extra vars it
      saves with those values.
    - With a survey but no `ask_variables_on_launch`, `--extra-vars` may carry
      only survey variables.
    - A missing required survey variable is a usage error; an empty
      `--extra-vars` mapping is never refused.
    - If AWX still reports `ignored_fields`, that row fails and keeps the job id.

    ## Dry runs

    `--dry-run` submits nothing and shows each target's resolved `payload`
    (names resolved to ids, `extra_vars` merged). It shows as `<redacted>`
    survey password answers and, at any depth, variables whose names look
    secret:

    - a word of the name (split at `_`, `-`, `.` and camelCase) is `pass`,
      `passwd`, `password`, `passphrase`, `pwd`, `secret` or `token`;
    - or the name ends in `password`, `passphrase`, `secret` or `token`
      (`dbpassword`);
    - or two adjacent words form `api_key`, `access_key`, `private_key`,
      `secret_key` or `ssh_key`.

    Examples
    --------
    ```console
    $ untaped awx job-templates launch Deploy --extra-vars @vars.yml --extra-vars v=2 --wait
    $ untaped awx job-templates launch Deploy --launch-inventory Staging --dry-run -f yaml
    $ untaped awx workflow-templates launch "Release train" --follow
    ```
    """

    extra_vars: list[str] = Field(
        default_factory=list,
        description=(
            "KEY=VAL (true/false/null, integers and JSON objects/arrays decoded; anything "
            "else stays a string), @FILE (YAML/JSON), or a JSON/YAML mapping; repeatable, "
            "merged left to right."
        ),
    )
    host_pattern: str | None = Field(
        None, description="Limit the run to hosts matching this pattern."
    )
    launch_inventory: str | None = Field(
        None, description="Inventory to run against: a name, or digits for an AWX id."
    )
    credential: list[str] = Field(
        default_factory=list,
        description="Override credential by name (repeatable; resolved to ids).",
    )
    scm_branch: str | None = Field(None, description="SCM branch to run from.")
    job_tag: list[str] = Field(
        default_factory=list, description="Run only tasks with these tags (repeatable)."
    )
    skip_tag: list[str] = Field(
        default_factory=list, description="Skip tasks with these tags (repeatable)."
    )
    verbosity: int | None = Field(None, description="0-4 (passed verbatim).")
    diff_mode: bool | None = Field(None, description="Override diff_mode for this run.")
    job_type: str | None = Field(None, description="Override job_type (e.g. run, check).")

    def run(self) -> Iterator[OutcomeRecord]:
        return self._run("launch", self._payload)

    def _payload(self, awx: Any) -> dict[str, Any]:
        accepts = launch_accepts(self.spec)
        scope = scope_for_command(awx, self.spec, scope_values(self.scope))
        supplied = {f.field: getattr(self, f.field, None) for f in LAUNCH_FLAGS}
        try:
            return _build_launch_payload(
                accepts=accepts,
                extra_vars=self.extra_vars,
                limit=self.host_pattern,
                supplied=supplied,
                fk=awx.fk,
                org_scope=scope,
            )
        except ResourceNotFoundError as exc:
            organization = scope_values(self.scope).get("organization")
            raise _with_scope_note(exc, organization, scope) from None


def launch_accepts(spec: AwxResourceSpec) -> frozenset[str]:
    """The payload fields ``spec``'s launch accepts."""
    return next((a.accepts for a in spec.actions if a.name == "launch"), frozenset())


def unaccepted_launch_fields(spec: AwxResourceSpec) -> tuple[str, ...]:
    """The launch options ``spec`` never takes: its group's ``launch`` leaves them out."""
    accepts = launch_accepts(spec)
    return tuple(f.field for f in LAUNCH_FLAGS if f.accepts_key not in accepts)


@dataclass(frozen=True)
class LaunchFlag:
    """One row of the launch-flag dispatch table.

    Each launch option (a field of :class:`Launch`) has two concerns: whether
    a kind takes it (a kind's ``launch`` leaves out the fields its
    ``ActionSpec.accepts`` lacks, so the flag is unknown there) and how its
    value becomes the AAP-side payload field. ``LaunchFlag`` holds both in
    one row, so adding an option is one tuple entry and one field.

    The inline ``payload_builder`` is a deliberate departure from the
    project's usual ``apply_strategy: str`` + resolver pattern (see
    :class:`ResourceSpec`). That pattern earns its keep by keeping
    ``domain/`` pure of behaviour selectors; ``LaunchFlag`` lives with the
    commands (the composition root), where the closures are trivial and
    not independently injectable.
    """

    field: str
    accepts_key: str
    payload_builder: Callable[[Any, FkResolver, dict[str, str] | None], Any]


def _with_scope_note(
    exc: ResourceNotFoundError, organization: str | None, scope: dict[str, str] | None
) -> ResourceNotFoundError:
    """A missed FK name says so when ``awx.default_organization`` chose its scope."""
    default = (scope or {}).get("organization") if organization is None else None
    return exc.with_note(default_organization_note(default)) if default else exc


def _inventory_id(value: str, fk: FkResolver, scope: dict[str, str] | None) -> Any:
    """``--launch-inventory``: digits are an AWX id, anything else a name in scope."""
    if value.isdecimal():
        return fk.validate_id("Inventory", int(value))
    return fk.name_to_id("Inventory", value, scope=scope)


# Source of truth for the launch CLI flag → payload-field mapping.
# ``extra_vars`` and ``limit`` stay outside the table — both are
# accepted by every launch-capable kind today, so they don't need
# per-kind visibility / rejection logic. If a future kind drops one,
# fold it in here.
LAUNCH_FLAGS: tuple[LaunchFlag, ...] = (
    LaunchFlag("launch_inventory", "inventory", _inventory_id),
    LaunchFlag(
        "credential",
        "credentials",
        lambda v, fk, scope: [fk.name_to_id("Credential", c, scope=scope) for c in v],
    ),
    LaunchFlag("scm_branch", "scm_branch", lambda v, _fk, _scope: v),
    LaunchFlag("job_tag", "job_tags", lambda v, _fk, _scope: ",".join(v)),
    LaunchFlag("skip_tag", "skip_tags", lambda v, _fk, _scope: ",".join(v)),
    LaunchFlag("verbosity", "verbosity", lambda v, _fk, _scope: v),
    LaunchFlag("diff_mode", "diff_mode", lambda v, _fk, _scope: v),
    LaunchFlag("job_type", "job_type", lambda v, _fk, _scope: v),
)


def _is_supplied(value: object) -> bool:
    # ``None`` (default) and ``[]`` (repeatable flag not given) mean
    # "not supplied". ``False`` (``--no-diff-mode``) and ``0``
    # (``--verbosity 0``) ARE supplied and must round-trip — so this
    # is not ``bool(value)``.
    return value is not None and value != []


def _build_launch_payload(
    *,
    accepts: frozenset[str],
    extra_vars: list[str],
    limit: str | None,
    supplied: dict[str, object],
    fk: FkResolver,
    org_scope: dict[str, str] | None,
) -> dict[str, Any]:
    """Translate the launch CLI flags into the payload AAP expects.

    Only fields listed in this kind's ``ActionSpec.accepts`` are
    forwarded (a kind's ``launch`` has no option for the others). FK flags
    (``--launch-inventory``, ``--credential``) resolve names to ids using the
    per-process :class:`FkResolver` via each row's ``payload_builder``.
    """
    payload: dict[str, Any] = {}
    if extra_vars and "extra_vars" in accepts:
        payload["extra_vars"] = encode_extra_vars(parse_extra_vars(extra_vars))
    if limit and "limit" in accepts:
        payload["limit"] = limit
    for f in LAUNCH_FLAGS:
        if f.accepts_key not in accepts:
            continue
        value = supplied.get(f.field)
        if not _is_supplied(value):
            continue
        payload[f.accepts_key] = f.payload_builder(value, fk, org_scope)
    return payload


_KEY_VALUE = re.compile(r"^[A-Za-z_][\w.-]*=")
_INTEGER = re.compile(r"^-?(0|[1-9]\d*)$")
_JSON_WORDS: dict[str, Any] = {"true": True, "false": False, "null": None}


def _decode_key_value(raw: str) -> Any:
    """Decode ``true``/``false``/``null``, integers, and JSON objects/arrays.

    Everything else (``1.10``, ``1e3``, ``NaN``, quoted text) stays the
    literal string the user typed, so version-like values are not mangled.
    """
    if raw in _JSON_WORDS:
        return _JSON_WORDS[raw]
    if _INTEGER.match(raw):
        return int(raw)
    if raw[:1] in {"{", "["}:
        try:
            decoded = json.loads(raw, parse_constant=_reject_constant)
        except json.JSONDecodeError, ValueError:
            return raw
        if isinstance(decoded, dict | list):
            return decoded
    return raw


def _reject_constant(name: str) -> Any:
    raise ValueError(name)


def _normalize(value: Any) -> Any:
    """Turn YAML timestamps into ISO strings so the mapping is JSON-encodable."""
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _normalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return value


def encode_extra_vars(values: dict[str, Any]) -> str:
    """JSON-encode merged extra vars, rejecting values JSON cannot carry."""
    try:
        return json.dumps(_normalize(values), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise UsageError(f"--extra-vars contains a value JSON cannot encode: {exc}") from exc


def parse_extra_vars(values: list[str]) -> dict[str, Any]:
    """Merge ``--extra-vars`` entries (left to right) into one mapping.

    Each entry is ``@PATH`` (a YAML/JSON mapping file), ``KEY=VAL`` (the
    value decoded only for ``true``/``false``/``null``, integers, and JSON
    objects/arrays; anything else is kept as the typed string), or a raw
    JSON/YAML mapping. AWX expects a mapping; anything else is a usage error.
    """
    merged: dict[str, Any] = {}
    for entry in values:
        if entry.startswith("@"):
            merged.update(read_structured_file(Path(entry[1:]).expanduser()))
        elif _KEY_VALUE.match(entry):
            key, _, raw = entry.partition("=")
            merged[key] = _decode_key_value(raw)
        else:
            try:
                parsed = yaml.safe_load(entry)
            except yaml.YAMLError:
                parsed = None
            if not isinstance(parsed, dict):
                raise UsageError(
                    f"--extra-vars expects KEY=VAL, @FILE, or a JSON/YAML mapping (got {entry!r})"
                )
            merged.update(parsed)
    return merged


__all__ = [
    "LAUNCH_FLAGS",
    "Launch",
    "LaunchFlag",
    "Sync",
    "encode_extra_vars",
    "parse_extra_vars",
    "unaccepted_launch_fields",
]
