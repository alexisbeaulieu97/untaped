"""Root ``untaped doctor`` command.

A terminal command (not a group): it runs the shell plus every composed
capability's health checks OFFLINE — config-file reads plus in-process model
validation only, never network I/O. Each row is isolated: invalid settings
for one capability surface as failed rows while every other row still runs.
Quarantine records render as failed rows (nonzero exit).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from cyclopts import App
from pydantic import BaseModel, ValidationError

from untaped.capabilities.registry import (
    ApplicationSpec,
    CapabilityContext,
    CompositionResult,
    DoctorCheck,
    DoctorResult,
    QuarantineRecord,
)
from untaped.cli import (
    ColumnsOption,
    FormatOption,
    create_app,
    echo,
    report_errors,
)
from untaped.config_file import read_config_dict
from untaped.errors import ConfigError, first_validation_error
from untaped.management._render import emit_isolated
from untaped.profile_resolver import classify_active_profile
from untaped.render import OutputFormat
from untaped.settings import (
    Settings,
    active_settings_layout,
    check_settings_field,
    resolve_config_path,
)
from untaped.theme import UiSettings, resolve_theme

_PASS = "pass"
_FAIL = "fail"


@dataclass(frozen=True)
class _SectionScope:
    """One section's health scope: models plus the check bodies owning it."""

    capability: str
    section: str
    profile_model: type[BaseModel]
    state_model: type[BaseModel] | None
    checks: tuple[DoctorCheck, ...]


def build_root_doctor_app(*, shell: ApplicationSpec, result: CompositionResult) -> App:
    """Return the root ``doctor`` terminal command for one composition."""
    app = create_app(
        name="doctor",
        help="Check the health of the shell and every composed capability.",
    )

    @app.default
    def run_command(
        *,
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """Run every health check and report one row per check."""
        with report_errors():
            _run(shell, result, fmt=fmt, columns=columns)

    return app


def _run(
    shell: ApplicationSpec,
    result: CompositionResult,
    *,
    fmt: OutputFormat,
    columns: list[str] | None,
) -> None:
    rows = _collect(shell, result)
    emit_isolated(rows, fmt=fmt, columns=columns)
    failed = [row for row in rows if row["status"] == _FAIL]
    if failed:
        echo(f"doctor: {len(failed)} of {len(rows)} checks failed", err=True)
        raise SystemExit(1)


def _row(check: str, capability: str, status: str, title: str, detail: str) -> dict[str, object]:
    return {
        "check": check,
        "capability": capability,
        "status": status,
        "title": title,
        "detail": detail,
    }


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
    for registered in result.capabilities:
        spec = registered.spec
        scopes.append(
            _SectionScope(
                capability=spec.name,
                section=spec.config_section,
                profile_model=spec.profile_model,
                state_model=spec.state_model,
                checks=tuple(spec.doctor_checks),
            )
        )
    return scopes


def _collect(shell: ApplicationSpec, result: CompositionResult) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    raw, config_row = _config_row(shell)
    rows.append(config_row)
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
    for scope in _scopes(shell, result):
        settings, error = _validate_section(scope, effective, settings_error)
        if error is not None:
            rows.append(_row("settings", scope.capability, _FAIL, "validate settings", error))
        else:
            rows.append(
                _row("settings", scope.capability, _PASS, "validate settings", "settings OK")
            )
        contexts.append((scope, settings))
        if scope.state_model is not None and raw is not None:
            rows.append(_state_row(scope, scope.state_model, raw))
    for scope, settings in contexts:
        for check_item in scope.checks:
            rows.append(_run_check(scope, check_item, settings))
    for record in result.quarantine:
        rows.append(_quarantine_row(record))
    return rows


def _config_row(shell: ApplicationSpec) -> tuple[dict[str, Any] | None, dict[str, object]]:
    path = resolve_config_path()
    try:
        raw = read_config_dict(path)
    except ConfigError as exc:
        return None, _row("config", shell.name, _FAIL, "load config file", str(exc))
    return raw, _row("config", shell.name, _PASS, "load config file", str(path))


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
    """Validate one core setting (``log_level``/``http``/``ui``) plus env overrides."""
    title = f"validate {field}"
    if effective is None:
        return _row("settings", shell.name, _FAIL, title, settings_error or "config unreadable")
    try:
        value = check_settings_field(field, effective.get(field))
        if isinstance(value, UiSettings):
            resolve_theme(value)
    except ConfigError as exc:
        return _row("settings", shell.name, _FAIL, title, str(exc))
    return _row("settings", shell.name, _PASS, title, "settings OK")


def _state_row(
    scope: _SectionScope, state_model: type[BaseModel], raw: Mapping[str, Any]
) -> dict[str, object]:
    """Validate a capability's top-level state section (profile-independent)."""
    title = "validate state"
    node = raw.get(scope.section)
    if node is None:
        return _row("state", scope.capability, _PASS, title, "no state")
    if not isinstance(node, dict):
        detail = f"state section {scope.section!r} must be a mapping"
        return _row("state", scope.capability, _FAIL, title, detail)
    try:
        state_model.model_validate(node)
    except ValidationError as exc:
        detail = f"invalid {scope.section} state: {first_validation_error(exc)}"
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
    scope: _SectionScope, check_item: DoctorCheck, settings: BaseModel | None
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
    if not outcome.ok:
        return _row(check_item.id, scope.capability, _FAIL, check_item.title, outcome.detail)
    return _row(check_item.id, scope.capability, _PASS, check_item.title, outcome.detail or "OK")


def _quarantine_row(record: QuarantineRecord) -> dict[str, object]:
    detail = record.detail
    if record.entry_point:
        detail = f"{detail} [entry point {record.entry_point}]"
    return _row("quarantine", record.distribution, _FAIL, record.reason, detail)


__all__ = ["build_root_doctor_app"]
