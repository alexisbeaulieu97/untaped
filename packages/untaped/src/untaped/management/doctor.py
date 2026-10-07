"""Root ``untaped doctor`` command group.

Its default command runs the shell plus every composed capability's health
checks OFFLINE — config-file reads plus in-process model
validation only, never network I/O. ``--online`` adds the checks
capabilities contribute with ``DoctorCheck(online=True)``, which contact the
configured services. A check's ``DoctorResult.fix`` becomes the row's
``fix``: a complete argv (``--profile`` first) to run after ``untaped``;
the human view shows it under its row as the command line to type. A row's
``automatic`` says the fix is safe to run unattended (see ``DoctorResult``).
Each row is isolated: invalid settings for one capability surface as failed
rows while every other row still runs.
Quarantine records render as failed rows (nonzero exit). A config file other
users can read renders as a ``warn`` row, which does not fail the run; so do
profile keys no settings model declares, renamed, retired or deprecated keys
in any profile (fixed by ``config migrate``, except deprecated settings),
installed skills that differ from their packaged copy, and a capability
check that returns ``DoctorResult(..., warn=True)``. ``doctor fix``
(:mod:`untaped.management.fix`) runs every automatic fix the checks name.
"""

from __future__ import annotations

import os
import re
import shlex
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

from cyclopts import App, Parameter
from pydantic import BaseModel, ValidationError

from untaped.capabilities.registry import (
    ApplicationSpec,
    CapabilityContext,
    CapabilitySpec,
    CompositionResult,
    DoctorCheck,
    DoctorResult,
    QuarantineRecord,
    run_deferred_factory,
)
from untaped.cli import (
    ColumnsOption,
    DryRunOption,
    FormatOption,
    YesOption,
    create_app,
    echo,
    report_errors,
    writes,
)
from untaped.config_file import read_config_dict
from untaped.config_schema import walk_settings
from untaped.deprecated_keys import key_mappings, scan_keys
from untaped.errors import ConfigError, ExitCode, first_validation_error
from untaped.http import resolve_verify
from untaped.management._render import emit_check_list, emit_isolated
from untaped.management.skills import composed_skills
from untaped.messages import command_argv, command_line, hint, plural, summary
from untaped.profile_resolver import (
    classify_active_profile,
    profile_override,
    profile_scope,
    selected_profile,
)
from untaped.settings import (
    RESERVED_STATE_SECTIONS,
    HttpSettings,
    Settings,
    active_settings_layout,
    check_settings_field,
    get_profile_settings_model,
    profile_section_models,
    resolve_config_path,
    resolve_state_path,
    resolve_with_overlay,
    section_stabilities,
    settings_overlay,
)
from untaped.skills import SkillState, outdated_skills, project_root
from untaped.theme import OutputFormat, UiSettings, check_declared_tokens, resolve_theme
from untaped.token_store import pass_problem

_PASS = "pass"
_FAIL = "fail"
_WARN = "warn"
_STATUSES = (_PASS, _WARN, _FAIL)
_PLACEHOLDER = re.compile(r"<[A-Z][A-Z0-9_]*>")


@dataclass(frozen=True)
class _SectionScope:
    """One section's health scope: models plus the check bodies owning it."""

    capability: str
    section: str
    profile_model: type[BaseModel]
    state_model: type[BaseModel] | None
    checks: tuple[DoctorCheck, ...]


OnlineOption = Annotated[
    bool,
    Parameter(
        name="--online",
        negative="",
        help="Also contact each configured service (tokens, URLs, TLS).",
    ),
]


def build_root_doctor_app(
    *,
    shell: ApplicationSpec,
    result: CompositionResult,
    builtin_for: Callable[[str], str | None],
) -> App:
    """Return the root ``doctor`` command and its ``fix`` subcommand for one composition.

    ``builtin_for`` resolves a root command name, as dispatch would.
    """
    app = create_app(
        name="doctor",
        help="Check the health of the shell and every composed capability.",
    )

    @app.default
    def run_command(
        *, online: OnlineOption = False, fmt: FormatOption = "table", columns: ColumnsOption = None
    ) -> None:
        """Run every health check and report one row per check."""
        with report_errors():
            _run(shell, result, online=online, fmt=fmt, columns=columns)

    @app.command(name="fix")
    @writes(destructive=True)
    def fix_command(
        *,
        online: OnlineOption = False,
        yes: YesOption = False,
        dry_run: DryRunOption = False,
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """Run every automatic fix doctor found, after one confirmation."""
        from untaped.management.fix import run_fixes  # noqa: PLC0415 - fix imports doctor

        with report_errors():
            run_fixes(
                shell,
                result,
                online=online,
                yes=yes,
                dry_run=dry_run,
                fmt=fmt,
                columns=columns,
                builtin_for=builtin_for,
            )

    return app


def _run(
    shell: ApplicationSpec,
    result: CompositionResult,
    *,
    online: bool,
    fmt: OutputFormat,
    columns: list[str] | None,
) -> None:
    rows = collect_doctor_rows(shell, result, online=online)
    report_check_rows(rows, op="doctor", fmt=fmt, columns=columns)


def report_check_rows(
    rows: list[dict[str, object]], *, op: str, fmt: OutputFormat, columns: list[str] | None
) -> None:
    """Emit ``untaped.doctor_check`` rows and an ``op`` footer; exit 1 when any failed.

    ``table`` prints the checklist; with ``--columns`` it prints the table,
    whose ``fix`` is the command line to type.
    """
    profile = selected_profile()
    if fmt == "table" and columns is None:
        emit_check_list(rows, run_line=lambda argv: run_line(argv, profile))
        echo(err=True)
    else:
        shown = [_table_row(row, profile) for row in rows] if fmt == "table" else rows
        emit_isolated(shown, fmt=fmt, columns=columns, kind="untaped.doctor_check")
    counts = {status: sum(row["status"] == status for row in rows) for status in _STATUSES}
    echo(summary(op, counts), err=True)
    fix_hint = _fix_hint(rows, profile)
    if fix_hint:
        echo(fix_hint, err=True)
    if counts[_FAIL]:
        raise SystemExit(ExitCode.FAILURE)


def _fix_hint(rows: list[dict[str, object]], profile: str) -> str | None:
    """``hint: run `untaped doctor fix` …`` when an automatic fix exists, else ``None``.

    Fixes are counted as ``doctor fix`` groups them; the command keeps the
    rows' profile.
    """
    fixes = {argv: is_automatic(covered) for argv, covered in group_fixes(rows).items()}
    automatic = [argv for argv, auto in fixes.items() if auto]
    if not automatic:
        return None
    first = automatic[0]
    target = first[1] if first[:1] == ("--profile",) and len(first) > 1 else profile
    command = run_line(command_argv("doctor fix", profile=target), profile)
    text = f"{hint(command)} to apply {plural(len(automatic), 'automatic fix', 'automatic fixes')}"
    manual = len(fixes) - len(automatic)
    if manual:
        text = f"{text}; {plural(manual, 'fix', 'fixes')} {'needs' if manual == 1 else 'need'} you"
    return text


def _table_row(row: dict[str, object], profile: str) -> dict[str, object]:
    fix = row.get("fix")
    return {**row, "fix": run_line(fix, profile) if isinstance(fix, list) and fix else ""}


def run_line(argv: list[str], profile: str) -> str:
    """A fix or step argv as the ``untaped …`` line a person types.

    ``--profile <profile>`` goes without saying when no ``--profile`` flag
    chose it: the same line then acts on that profile anyway.
    """
    if argv[:2] == ["--profile", profile] and profile_override() is None:
        argv = argv[2:]
    return command_line(shlex.join(argv))


def _row(
    check: str,
    capability: str,
    status: str,
    title: str,
    detail: str,
    fix: list[str] | None = None,
    *,
    automatic: bool = False,
) -> dict[str, object]:
    return {
        "check": check,
        "capability": capability,
        "status": status,
        "title": title,
        "detail": detail,
        "fix": fix,
        "automatic": bool(automatic) and fix is not None,
    }


def placeholders(argv: list[str]) -> list[str]:
    """The ``<NAME>`` values ``argv`` still needs, in order."""
    return [found for arg in argv for found in _PLACEHOLDER.findall(arg)]


def group_fixes(rows: list[dict[str, object]]) -> dict[tuple[str, ...], list[dict[str, object]]]:
    """Warned and failed rows with a fix, keyed by exact fix argv, first appearance first."""
    grouped: dict[tuple[str, ...], list[dict[str, object]]] = {}
    for row in rows:
        fix = row.get("fix")
        if row["status"] != _PASS and isinstance(fix, list) and fix:
            grouped.setdefault(tuple(str(arg) for arg in fix), []).append(row)
    return grouped


def is_automatic(covered: list[dict[str, object]]) -> bool:
    """A grouped fix is automatic only when every row naming it says so."""
    return all(row.get("automatic") for row in covered)


def _scopes(shell: ApplicationSpec, result: CompositionResult) -> list[_SectionScope]:
    scopes = [
        _SectionScope(
            capability=shell.name,
            section=shell.config_section,
            profile_model=shell.profile_model,
            state_model=shell.state_model,
            checks=tuple(shell.doctor_checks),
        )
    ]
    scopes.extend(_capability_scope(registered.spec) for registered in result.capabilities)
    return scopes


def _capability_scope(spec: CapabilitySpec) -> _SectionScope:
    return _SectionScope(
        capability=spec.name,
        section=spec.config_section,
        profile_model=spec.profile_model,
        state_model=spec.state_model,
        checks=tuple(spec.doctor_checks),
    )


def collect_doctor_rows(
    shell: ApplicationSpec,
    result: CompositionResult,
    *,
    online: bool = False,
    capabilities: frozenset[str] | None = None,
) -> list[dict[str, object]]:
    """Every doctor row, offline unless ``online``.

    ``capabilities`` limits the capability-contributed checks to those
    capabilities (``setup`` checks only what it configured).
    """
    rows: list[dict[str, object]] = []
    raw, config_row = _config_row(shell)
    rows.append(config_row)
    if raw is not None:
        rows.extend(_config_file_rows(shell, raw))
    state, state_file_row = _state_file_row(shell)
    rows.append(state_file_row)
    settings_error: str | None = None
    effective: Mapping[str, Any] | None = None
    if raw is None:
        settings_error = str(config_row["detail"])
    else:
        try:
            effective = active_settings_layout().effective(raw)
        except ConfigError as exc:
            settings_error = str(exc)
        rows.append(_profile_row(shell, raw, settings_error))
    for field in Settings.model_fields:
        rows.append(_core_row(shell, field, effective, settings_error))
    contexts: list[tuple[_SectionScope, BaseModel | None]] = []
    scopes = _scopes(shell, result)
    for scope in scopes:
        settings, error = _validate_section(scope, effective, settings_error)
        if error is not None:
            rows.append(_row("settings", scope.capability, _FAIL, "validate settings", error))
        else:
            rows.append(
                _row("settings", scope.capability, _PASS, "validate settings", "settings OK")
            )
        contexts.append((scope, settings))
        if scope.state_model is not None:
            rows.append(_state_row(scope, scope.state_model, state))
    profile = selected_profile(raw or {})
    rows.extend(_check_rows(contexts, online=online, capabilities=capabilities, profile=profile))
    rows.append(_skills_row(shell, result))
    for record in result.quarantine:
        rows.append(_quarantine_row(record))
    for registered in result.capabilities:
        if capabilities is not None and registered.spec.name not in capabilities:
            continue
        built = run_deferred_factory(registered)
        if isinstance(built, QuarantineRecord):
            rows.append(_quarantine_row(built))
    return rows


def selected_check_rows(
    shell: ApplicationSpec,
    result: CompositionResult,
    profile: str,
    selected: frozenset[str],
) -> list[dict[str, object]]:
    """``selected`` capabilities' doctor rows for ``profile``, online checks included."""
    with profile_scope(profile):
        rows = collect_doctor_rows(shell, result, online=True, capabilities=selected)
    # Old keys concern every capability's settings, so setup shows them too.
    return [
        row
        for row in rows
        if row["capability"] in selected
        or (row["check"] == "deprecated-keys" and row["status"] != _PASS)
    ]


def online_probe_rows(
    result: CompositionResult,
    profile: str,
    capability: str,
    values: Mapping[str, object],
) -> list[dict[str, object]]:
    """``capability``'s online checks against ``values``, which nothing has written yet.

    ``values`` are candidate settings for the capability's section in
    ``profile`` (a ``None`` value unsets that key), laid over the loaded config
    for the duration of the call (:func:`untaped.settings.settings_overlay`);
    ``profile`` need not exist yet. Only the ``online=True`` checks run: the
    offline ones (connection settings, the plaintext-token warning) would
    misreport on candidate values, and there are no state or config-file rows.
    Settings that do not validate come back as the one failing ``validate
    settings`` row. Never writes: not the config, not a token store.

    ``setup`` runs this on a worker thread inside a copy of its own context;
    the overlay lives in that copy only.
    """
    spec = next(
        (
            registered.spec
            for registered in result.capabilities
            if registered.spec.name == capability
        ),
        None,
    )
    if spec is None:
        raise ConfigError(f"no composed capability named {capability!r} to check")
    scope = _capability_scope(spec)
    with profile_scope(profile), settings_overlay(profile, scope.section, values):
        settings, error = _validate_section(scope, *_overlaid_effective())
        if error is not None:
            return [_row("settings", scope.capability, _FAIL, "validate settings", error)]
        return [
            _run_check(scope, check_item, settings, profile=profile)
            for check_item in scope.checks
            if check_item.online
        ]


def _overlaid_effective() -> tuple[dict[str, Any] | None, str | None]:
    """The selected profile's effective values with the overlay over them, or why not."""
    try:
        return resolve_with_overlay(read_config_dict()).effective, None
    except ConfigError as exc:
        return None, str(exc)


def _check_rows(
    contexts: list[tuple[_SectionScope, BaseModel | None]],
    *,
    online: bool,
    capabilities: frozenset[str] | None,
    profile: str,
) -> list[dict[str, object]]:
    """Run the contributed checks (online ones only when ``online``)."""
    return [
        _run_check(scope, check_item, settings, profile=profile)
        for scope, settings in contexts
        if capabilities is None or scope.capability in capabilities
        for check_item in scope.checks
        if online or not check_item.online
    ]


def _permissions_row(shell: ApplicationSpec) -> dict[str, object]:
    """Warn when other users can read or write the config file (it may hold tokens)."""
    title = "config file permissions"
    path = resolve_config_path()
    if os.name == "nt" or not path.is_file():
        return _row("config", shell.name, _PASS, title, "not checked")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        detail = f"{path} has mode {mode:04o}; restrict it with `chmod 600 {path}`"
        return _row("config", shell.name, _WARN, title, detail)
    return _row("config", shell.name, _PASS, title, f"mode {mode:04o}")


def _config_file_rows(shell: ApplicationSpec, raw: dict[str, Any]) -> list[dict[str, object]]:
    rows = [
        _permissions_row(shell),
        _unknown_keys_row(shell, raw),
        _deprecated_keys_row(shell, raw),
    ]
    if (store_row := _token_store_row(shell, raw)) is not None:
        rows.append(store_row)
    return rows


def _token_store_row(shell: ApplicationSpec, raw: dict[str, Any]) -> dict[str, object] | None:
    """Fail when a ``pass`` token command cannot work here (no key, no gpg).

    Only checked once a profile's ``token_command`` runs ``pass``, so
    installing ``pass`` alone never adds a row.
    """
    layout = active_settings_layout()
    uses_pass = any(
        isinstance(node, dict)
        and isinstance(argv := node.get("token_command"), list)
        and argv
        and Path(str(argv[0])).name == "pass"
        for profile in layout.profile_names(raw)
        for node in (layout.profile_data(raw, profile) or {}).values()
    )
    if not uses_pass:
        return None
    title = "pass token store"
    if (problem := pass_problem()) is not None:
        return _row("config", shell.name, _FAIL, title, problem)
    return _row("config", shell.name, _PASS, title, "gpg holds a key for the password store")


def _unknown_keys_row(shell: ApplicationSpec, raw: Mapping[str, Any]) -> dict[str, object]:
    """Warn about keys no settings model reads: typos in profiles, stray top-level keys.

    Only ``active``, ``profiles`` and ``format_version`` are read at the top
    level, so anything else there (a pre-8.0 state section, ``log_level``) is
    flagged, not moved.
    """
    title = "unknown config keys"
    model = get_profile_settings_model()
    leaves = {d.path for d in walk_settings(model, include_collections=True)}
    # Old keys are the deprecated-keys row's to report.
    leaves |= {
        (section, *old.split("."))
        for section, section_model in profile_section_models().items()
        for old in key_mappings(section_model).migratable
    }
    prefixes = {path[:depth] for path in leaves for depth in range(1, len(path))}
    unknown = [str(key) for key in raw if key not in RESERVED_STATE_SECTIONS]
    profiles = raw.get("profiles")
    for name, data in profiles.items() if isinstance(profiles, dict) else ():
        if isinstance(data, dict):
            _collect_unknown(data, ("profiles", str(name)), (), leaves, prefixes, unknown)
    if unknown:
        return _row("unknown-keys", shell.name, _WARN, title, "ignored: " + ", ".join(unknown))
    return _row("unknown-keys", shell.name, _PASS, title, "no unknown keys")


def _deprecated_keys_row(shell: ApplicationSpec, raw: Mapping[str, Any]) -> dict[str, object]:
    """Warn about old keys and deprecated settings in any profile."""
    title = "deprecated config keys"
    found = scan_keys(raw, profile_section_models(), section_stabilities())
    if not found:
        return _row("deprecated-keys", shell.name, _PASS, title, "no deprecated keys")
    parts = []
    for item in found:
        old = f"{item.section}.{item.old}"
        if item.kind == "deprecated":
            advice = f": use {item.message}" if item.message else ""
            parts.append(f"{old} (profile {item.profile}, deprecated){advice}")
        else:
            retired = ", retired" if item.kind == "retired" else ""
            parts.append(f"{old} (profile {item.profile}{retired}) → {item.section}.{item.new}")
    fix = None
    if any(item.kind != "deprecated" for item in found):
        fix = command_argv("config migrate", profile=selected_profile(dict(raw)))
    return _row("deprecated-keys", shell.name, _WARN, title, "; ".join(parts), fix, automatic=True)


def _collect_unknown(
    node: Mapping[str, Any],
    location: tuple[str, ...],
    path: tuple[str, ...],
    leaves: set[tuple[str, ...]],
    prefixes: set[tuple[str, ...]],
    unknown: list[str],
) -> None:
    for key, value in node.items():
        child = (*path, str(key))
        if child in leaves:
            continue
        if child in prefixes:
            if isinstance(value, dict):
                _collect_unknown(value, location, child, leaves, prefixes, unknown)
            continue
        unknown.append(".".join((*location, *child)))


def _skills_row(shell: ApplicationSpec, result: CompositionResult) -> dict[str, object]:
    """Warn when an installed skill no longer matches the packaged copy."""
    title = "installed skills up to date"
    stale = outdated_skills(composed_skills(shell, result), project_dir=project_root(Path.cwd()))
    if not stale:
        return _row("skills", shell.name, _PASS, title, "no outdated skills")
    parts = [f"{item.state.value}: {item.path}" for item in stale]
    fix = None
    if any(item.state is SkillState.outdated for item in stale):
        fix = command_argv("skills update", profile=selected_profile())
    if any(item.state is SkillState.orphaned for item in stale):
        parts.append("remove unshipped skills with `untaped skills remove NAME`")
    detail = "; ".join(parts)
    return _row("skills", shell.name, _WARN, title, detail, fix, automatic=True)


def _config_row(shell: ApplicationSpec) -> tuple[dict[str, Any] | None, dict[str, object]]:
    path = resolve_config_path()
    try:
        raw = read_config_dict(path)
    except ConfigError as exc:
        return None, _row("config", shell.name, _FAIL, "load config file", str(exc))
    return raw, _row("config", shell.name, _PASS, "load config file", str(path))


def _state_file_row(
    shell: ApplicationSpec,
) -> tuple[tuple[dict[str, Any], Path] | None, dict[str, object]]:
    """Load ``state.yml``; return ``((raw, path), row)`` or ``(None, failed row)``."""
    title = "load state file"
    try:
        path = resolve_state_path()
        raw = read_config_dict(path)
    except ConfigError as exc:
        return None, _row("config", shell.name, _FAIL, title, str(exc))
    return (raw, path), _row("config", shell.name, _PASS, title, str(path))


def _profile_row(
    shell: ApplicationSpec, raw: dict[str, Any], settings_error: str | None
) -> dict[str, object]:
    """The selected profile (flag/env/``active:``) must exist."""
    title = "resolve active profile"
    if settings_error is not None:
        return _row("profile", shell.name, _FAIL, title, settings_error)
    name, source = classify_active_profile(raw)
    return _row("profile", shell.name, _PASS, title, f"{name or 'default'} (via {source})")


def _core_row(
    shell: ApplicationSpec,
    field: str,
    effective: Mapping[str, Any] | None,
    settings_error: str | None,
) -> dict[str, object]:
    """Validate one core setting (``http``/``ui``/``skills``) plus env overrides."""
    title = f"validate {field}"
    if effective is None:
        return _row("settings", shell.name, _FAIL, title, settings_error or "config unreadable")
    try:
        value = check_settings_field(field, effective.get(field))
        if isinstance(value, UiSettings):
            resolve_theme(value)
            check_declared_tokens(value)
        if isinstance(value, HttpSettings):
            resolve_verify(value)
    except ConfigError as exc:
        return _row("settings", shell.name, _FAIL, title, str(exc))
    return _row("settings", shell.name, _PASS, title, "settings OK")


def _state_row(
    scope: _SectionScope,
    state_model: type[BaseModel],
    state: tuple[dict[str, Any], Path] | None,
) -> dict[str, object]:
    """Validate a capability's state section in ``state.yml`` (profile-independent)."""
    title = "validate state"
    if state is None:
        return _row("state", scope.capability, _FAIL, title, "state file could not be read")
    state_raw, source = state
    node = state_raw.get(scope.section)
    if node is None:
        return _row("state", scope.capability, _PASS, title, "no state")
    if not isinstance(node, dict):
        detail = f"state section {scope.section!r} in {source} must be a mapping"
        return _row("state", scope.capability, _FAIL, title, detail)
    try:
        state_model.model_validate(node)
    except ValidationError as exc:
        detail = f"invalid {scope.section} state in {source}: {first_validation_error(exc)}"
        return _row("state", scope.capability, _FAIL, title, detail)
    return _row("state", scope.capability, _PASS, title, "state OK")


def _validate_section(
    scope: _SectionScope,
    effective: Mapping[str, Any] | None,
    settings_error: str | None,
) -> tuple[BaseModel | None, str | None]:
    """Validate one section's effective slice without touching other sections.

    ``UNTAPED_*`` env overrides are layered over the YAML slice, so a bad
    override fails this row and names the variable. Returns
    ``(settings, error)``: ``settings`` is the validated snapshot for check
    bodies (``None`` when unavailable), ``error`` the failed-row detail
    (``None`` when valid). Only YAML reads plus in-process validation run
    here — never network I/O.
    """
    if effective is None:
        return None, settings_error or "config file could not be read"
    node = effective.get(scope.section, {})
    if not isinstance(node, dict):
        return None, f"section {scope.section!r} must be a mapping"
    try:
        settings = check_settings_field(scope.section, node, model=scope.profile_model)
    except ConfigError as exc:
        return None, str(exc)
    return settings, None


def _run_check(
    scope: _SectionScope, check_item: DoctorCheck, settings: BaseModel | None, *, profile: str
) -> dict[str, object]:
    ctx = CapabilityContext(
        capability=scope.capability,
        config_section=scope.section,
        profile_fields=frozenset(scope.profile_model.model_fields),
        state_fields=frozenset(scope.state_model.model_fields if scope.state_model else ()),
        settings=settings,
    )
    try:
        # Typed as ``object``: provider bodies may return anything at runtime
        # (spec §5 row 9); the shape checks below are the validation.
        outcome: object = check_item.run(ctx)
    except Exception as exc:
        return _row(
            check_item.id,
            scope.capability,
            _FAIL,
            check_item.title,
            f"check raised {type(exc).__name__}: {exc}",
        )
    if not isinstance(outcome, DoctorResult):
        return _row(
            check_item.id,
            scope.capability,
            _FAIL,
            check_item.title,
            f"check returned {type(outcome).__name__}, expected DoctorResult",
        )
    if outcome.id != check_item.id:
        return _row(
            check_item.id,
            scope.capability,
            _FAIL,
            check_item.title,
            f"check returned id {outcome.id!r}, expected {check_item.id!r}",
        )
    fix = None
    # A passing row drops its fix, but an automatic one is still validated.
    if outcome.fix and (not outcome.ok or outcome.warn or outcome.automatic):
        try:
            fix = command_argv(outcome.fix, profile=profile)
        except ValueError as exc:
            detail = f"check returned an unparsable fix: {exc}"
            return _row(check_item.id, scope.capability, _FAIL, check_item.title, detail)
    if outcome.automatic and (fix is None or placeholders(fix)):
        needs = f"that needs {', '.join(placeholders(fix))}" if fix else "but no fix"
        detail = f"check {check_item.id} declares an automatic fix {needs}"
        return _row(check_item.id, scope.capability, _FAIL, check_item.title, detail)
    title = check_item.title
    if outcome.ok and not outcome.warn:
        return _row(check_item.id, scope.capability, _PASS, title, outcome.detail or "OK")
    status = _FAIL if not outcome.ok else _WARN
    detail = outcome.detail
    return _row(
        check_item.id, scope.capability, status, title, detail, fix, automatic=outcome.automatic
    )


def _quarantine_row(record: QuarantineRecord) -> dict[str, object]:
    origin = f"distribution {record.distribution}"
    if record.entry_point:
        origin = f"{origin}, entry point {record.entry_point}"
    return _row("quarantine", record.name, _FAIL, record.reason, f"{record.detail} [{origin}]")


__all__ = [
    "build_root_doctor_app",
    "collect_doctor_rows",
    "online_probe_rows",
    "report_check_rows",
    "selected_check_rows",
]
