"""``untaped doctor``'s rows: core's and every composed plugin's, offline or online.

:func:`collect_doctor_rows` gathers every row, each isolated so one
plugin's invalid settings never stop another's, plus with ``online=True``
the checks that contact a service (:func:`online_probe_rows`);
:func:`selected_check_rows` is the rows setup shows for the plugins it set up.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from untaped.core.commands.config import Migrate
from untaped.core.commands.skills import Remove as RemoveSkill
from untaped.core.commands.skills import Update as UpdateSkills
from untaped.core.commands.skills import composed_skills
from untaped.core.doctor_row import (
    FAIL,
    PASS,
    WARN,
    SectionScope,
    doctor_row,
    placeholders,
)
from untaped.core.doctor_stores import caches_row, plugin_folders_row, references_row, stores_row
from untaped.core.settings import NAME, CoreSettings
from untaped.core.skills import SkillState, local_project_root, outdated_skills
from untaped.runtime.config_file import read_config_dict
from untaped.runtime.profile_resolver import current_pick, fix_target
from untaped.runtime.registry import (
    CompositionResult,
    QuarantineRecord,
    build_plugin_app,
    section_settings,
)
from untaped.runtime.run import child_run
from untaped.runtime.settings import (
    HttpSettings,
    active_settings_layout,
    check_settings_field,
    profile_section_models,
    resolve_config_path,
    resolve_with_overlay,
    section_stabilities,
)
from untaped.runtime.settings_env import retired_env_vars
from untaped.runtime.theme import UiSettings, check_declared_tokens, resolve_theme
from untaped.sdk._run import SettingsOverlay
from untaped.sdk.errors import ConfigError, UntapedError
from untaped.sdk.health._setup_status import SETUP_CHECK, setup_result
from untaped.sdk.health.deprecated_keys import scan_keys
from untaped.sdk.health.doctor import DoctorCheck, DoctorResult, PluginContext
from untaped.sdk.health.marks import section_marks
from untaped.sdk.marks import owner, removal_text
from untaped.sdk.network.http import resolve_verify
from untaped.sdk.storage.state import ProjectStateRows, StateRows
from untaped.sdk.words import command_argv, command_line, removal_phrase

if TYPE_CHECKING:
    from untaped.runtime.composed import ComposedPlugin
    from untaped.sdk.projects import Project


def _scopes(result: CompositionResult) -> list[SectionScope]:
    # The root's parts get a row each (_core_row), so its checks get no settings: one
    # reads its part with load_core_part.
    return [
        _plugin_scope(
            plugin,
            settings=not plugin.root,
            held=tuple(block for _, block in result.mount.held(plugin.name, "state-rows")),
        )
        for plugin in result.plugins
    ]


def _plugin_scope(
    plugin: ComposedPlugin,
    *,
    settings: bool = True,
    held: tuple[StateRows[Any] | ProjectStateRows[Any], ...] = (),
) -> SectionScope:
    """``plugin``'s scope; without ``settings`` no section row. ``held``: its class's state rows."""
    return SectionScope(
        plugin=plugin.name,
        section=plugin.name,
        settings_model=section_settings(plugin) if settings else None,
        checks=tuple(plugin.doctor_checks),
        rows=held,
    )


def collect_doctor_rows(
    result: CompositionResult,
    *,
    online: bool = False,
    plugins: frozenset[str] | None = None,
) -> list[dict[str, object]]:
    """Every doctor row, offline unless ``online``.

    ``plugins`` limits the plugin-contributed checks to those
    plugins (``setup`` checks only what it configured).
    """
    rows: list[dict[str, object]] = []
    raw, config_row = _config_row()
    rows.append(config_row)
    if raw is not None:
        rows.append(_deprecated_keys_row(raw))
    settings_error: str | None = None
    effective: Mapping[str, Any] | None = None
    if raw is None:
        settings_error = str(config_row["detail"])
    else:
        try:
            effective = active_settings_layout().effective(raw)
        except ConfigError as exc:
            settings_error = str(exc)
        rows.append(_profile_row(settings_error))
    for part in CoreSettings.model_fields:
        rows.append(_core_row(part, effective, settings_error))
    contexts: list[tuple[SectionScope, BaseModel | None]] = []
    scopes = _scopes(result)
    profile = fix_target()
    for scope in scopes:
        settings = _settings_row(scope, effective, settings_error, rows)
        contexts.append((scope, settings))
        rows.extend(_setup_rows(scope, settings, profile=profile))
        if scope.rows:
            rows.append(_state_row(scope))
    rows.extend(_check_rows(contexts, online=online, plugins=plugins, profile=profile))
    rows.extend(_contract_rows(result, profile, plugins))
    rows.append(_skills_row(result))
    if plugins is None:
        rows.append(references_row(result))
        rows.append(stores_row(result))
        rows.append(caches_row(result))
        rows.append(plugin_folders_row(result))
    for record in result.quarantine:
        rows.append(_quarantine_row(record))
    for plugin in result.plugins:
        selected = plugins is None or plugin.name in plugins
        if not plugin.has_commands or not selected:
            continue
        built = build_plugin_app(plugin)
        if isinstance(built, QuarantineRecord):
            rows.append(_quarantine_row(built))
    return rows


def selected_check_rows(
    result: CompositionResult,
    profile: str,
    selected: frozenset[str],
) -> list[dict[str, object]]:
    """``selected`` plugins' doctor rows for ``profile``, online checks included."""
    with child_run(profiles=profile):
        rows = collect_doctor_rows(result, online=True, plugins=selected)
    # Old keys concern every plugin's settings, so setup shows them too.
    return [
        row
        for row in rows
        if row["plugin"] in selected
        or (row["check"] == "deprecated-keys" and row["status"] != PASS)
    ]


def online_probe_rows(
    result: CompositionResult,
    profile: str,
    plugin: str,
    values: Mapping[str, object],
) -> list[dict[str, object]]:
    """``plugin``'s online checks against ``values``, which nothing has written yet.

    ``values`` are candidate settings for the plugin's section in
    ``profile`` (a ``None`` value unsets that key), laid over the loaded config
    for the duration of the call, in a child run carrying them
    (:func:`untaped.runtime.run.child_run`);
    ``profile`` need not exist yet. Only the ``online=True`` checks run: the
    offline ones would misreport on candidate values, and there are no state
    or config-file rows.
    Settings that do not validate come back as the one failing ``validate
    settings`` row. Never writes: not the config, not a token store.

    ``setup`` runs this on a worker thread inside a copy of its own context;
    the child run lives in that copy only.
    """
    composed = next((each for each in result.plugins if each.name == plugin), None)
    if composed is None:
        raise ConfigError(f"no composed plugin named {plugin!r} to check")
    scope = _plugin_scope(composed)
    overlay = SettingsOverlay(profile, scope.section, dict(values))
    with child_run(profiles=profile, overlay=overlay):
        settings, error = None, None
        if scope.settings_model is not None:
            settings, error = _validate_section(scope, scope.settings_model, *_overlaid_effective())
        if error is not None:
            return [doctor_row("settings", scope.plugin, FAIL, "validate settings", error)]
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
    contexts: list[tuple[SectionScope, BaseModel | None]],
    *,
    online: bool,
    plugins: frozenset[str] | None,
    profile: str,
) -> list[dict[str, object]]:
    """Run the contributed checks (online ones only when ``online``)."""
    return [
        _run_check(scope, check_item, settings, profile=profile)
        for scope, settings in contexts
        if plugins is None or scope.plugin in plugins
        for check_item in scope.checks
        if online or not check_item.online
    ]


def _deprecated_keys_row(raw: Mapping[str, Any]) -> dict[str, object]:
    """Warn about old keys and deprecated settings in any profile."""
    title = "deprecated config keys"
    found = [
        item
        for item in scan_keys(raw, profile_section_models(), section_stabilities())
        if item.kind != "deleted"
    ]
    variables = retired_env_vars(profile_section_models())
    if not found and not variables:
        return doctor_row("deprecated-keys", NAME, PASS, title, "no deprecated keys")
    parts = []
    for item in found:
        old = f"{item.section}.{item.old}"
        removal = removal_phrase(removal_text(item.removed_in, owner(item.section)))
        if item.kind == "deprecated":
            advice = f": use {item.message}" if item.message else ""
            parts.append(f"{old} (profile {item.profile}, {removal}){advice}")
        elif item.kind == "retired":
            parts.append(f"{old} (profile {item.profile}, retired) → {item.section}.{item.new}")
        else:
            parts.append(f"{old} (profile {item.profile}, {removal}) → {item.section}.{item.new}")
    for variable in variables:
        if variable.new is not None:
            parts.append(f"{variable.name} (environment, retired) → {variable.new}")
        else:
            parts.append(f"{variable.name} (environment): {variable.note}")
    fix = None
    if any(item.kind != "deprecated" for item in found):
        fix = command_argv(Migrate.spelled(), profile=fix_target())
    return doctor_row("deprecated-keys", NAME, WARN, title, "; ".join(parts), fix, automatic=True)


def _skills_row(result: CompositionResult) -> dict[str, object]:
    """Warn when an installed skill is outdated, edited, or shipped by no plugin here.

    A stub a newer untaped wrote is fine and not reported; the automatic fix
    (``skills update``) leaves it and edited stubs alone.
    """
    title = "installed skills up to date"
    stale = [
        item
        for item in outdated_skills(composed_skills(result), project_dir=local_project_root(None))
        if item.state is not SkillState.newer
    ]
    if not stale:
        return doctor_row("skills", NAME, PASS, title, "no outdated skills")
    parts = [f"{item.state.value} skill: {item.path}" for item in stale]
    fix = None
    if any(item.state is SkillState.outdated for item in stale):
        fix = command_argv(UpdateSkills.spelled(), profile=fix_target())
    if any(item.state is SkillState.edited for item in stale):
        parts.append(
            "edited stubs are never rewritten by a run: keep them, or replace one with"
            f" `{command_line(UpdateSkills.spelled('--force', 'NAME'))}`"
        )
    if any(item.state is SkillState.orphaned for item in stale):
        parts.append(
            "orphaned skills are shipped by no plugin installed here: install the plugin"
            f" that ships one, or remove it with `{command_line(RemoveSkill.spelled('NAME'))}`"
        )
    detail = "; ".join(parts)
    return doctor_row("skills", NAME, WARN, title, detail, fix, automatic=True)


def _config_row() -> tuple[dict[str, Any] | None, dict[str, object]]:
    path = resolve_config_path()
    try:
        raw = read_config_dict(path)
    except ConfigError as exc:
        detail = str(exc) if exc.hint is None else f"{exc}; {exc.hint}"
        return None, doctor_row("config", NAME, FAIL, "load config file", detail)
    return raw, doctor_row("config", NAME, PASS, "load config file", str(path))


def _profile_row(settings_error: str | None) -> dict[str, object]:
    """Each picked profile (flag or env) must exist."""
    title = "resolve picked profiles"
    if settings_error is not None:
        return doctor_row("profile", NAME, FAIL, title, settings_error)
    picks = current_pick()
    detail = ", ".join(f"{pick.name} (via {pick.source})" for pick in picks)
    return doctor_row("profile", NAME, PASS, title, detail or "default (via fallback)")


def _core_row(
    part: str,
    effective: Mapping[str, Any] | None,
    settings_error: str | None,
) -> dict[str, object]:
    """Validate one part of core's section (``untaped.http``/``ui``/``skills``) plus env overrides.

    Each part is read apart (:func:`~untaped.runtime.settings.load_core_part`),
    so each gets its own row.
    """
    title = f"validate {NAME}.{part}"
    if effective is None:
        return doctor_row("settings", NAME, FAIL, title, settings_error or "config unreadable")
    section = effective.get(NAME)
    if section is not None and not isinstance(section, dict):
        return doctor_row("settings", NAME, FAIL, title, f"section {NAME!r} must be a mapping")
    node = (section or {}).get(part)
    try:
        checked = check_settings_field(
            NAME, None if node is None else {part: node}, model=CoreSettings, part=part
        )
        value = getattr(checked, part)
        if isinstance(value, UiSettings):
            resolve_theme(value)
            check_declared_tokens(value)
        if isinstance(value, HttpSettings):
            resolve_verify(value)
    except ConfigError as exc:
        return doctor_row("settings", NAME, FAIL, title, str(exc))
    return doctor_row("settings", NAME, PASS, title, "settings OK")


def _state_row(scope: SectionScope) -> dict[str, object]:
    """Read a plugin's state rows, each by its own rules (profile-independent).

    A damaged row fails with its fix; inside a project, so do that project's rows.
    """
    title = "validate state"
    try:
        counts = _row_counts(scope.rows)
    except ConfigError as exc:
        detail = str(exc) if exc.hint is None else f"{exc}; {exc.hint}"
        return doctor_row("state", scope.plugin, FAIL, title, detail)
    if not counts:
        return doctor_row("state", scope.plugin, PASS, title, "no state")
    return doctor_row("state", scope.plugin, PASS, title, "; ".join(["state OK", *counts]))


def _row_counts(declared: Sequence[StateRows[Any] | ProjectStateRows[Any]]) -> list[str]:
    """``<plugin>.<name>: N rows`` for each declaration with rows; the first damaged one raises."""
    project = _current_project() if any(isinstance(d, ProjectStateRows) for d in declared) else None
    counts: list[str] = []
    for each in declared:
        if isinstance(each, ProjectStateRows):
            if project is None:
                continue
            rows, where = each.of(project).all(), f" in project {project.name}"
        else:
            rows, where = each.all(), ""
        if rows:
            counts.append(f"{each.qualified}{where}: {len(rows)} row{'s' if len(rows) > 1 else ''}")
    return counts


def _current_project() -> Project | None:
    """The project the working directory is in, when it can be read; doctor never stops on it."""
    from untaped.sdk.projects import project_at  # noqa: PLC0415 - runs only in doctor

    try:
        return project_at(Path.cwd())
    except UntapedError:
        return None


def _validate_section(
    scope: SectionScope,
    model: type[BaseModel],
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
        settings = check_settings_field(scope.section, node, model=model)
    except ConfigError as exc:
        return None, str(exc)
    return settings, None


def _settings_row(
    scope: SectionScope,
    effective: Mapping[str, Any] | None,
    settings_error: str | None,
    rows: list[dict[str, object]],
) -> BaseModel | None:
    """Append the scope's settings row and return its validated settings.

    A plugin that declares no settings gets no row and its checks get ``None``.
    """
    if scope.settings_model is None:
        return None
    settings, error = _validate_section(scope, scope.settings_model, effective, settings_error)
    if error is not None:
        rows.append(doctor_row("settings", scope.plugin, FAIL, "validate settings", error))
    else:
        rows.append(doctor_row("settings", scope.plugin, PASS, "validate settings", "settings OK"))
    return settings


def _setup_rows(
    scope: SectionScope, settings: BaseModel | None, *, profile: str
) -> list[dict[str, object]]:
    """Core's ``setup`` row for a plugin that marks what it needs; none for any other."""
    marks = section_marks(scope.section, scope.settings_model)
    if marks is None:
        return []
    outcome = setup_result(settings, marks, section=scope.section)
    fix = None if outcome.fix is None else command_argv(outcome.fix, profile=profile)
    if outcome.ok and not outcome.warn:
        return [doctor_row(SETUP_CHECK, scope.plugin, PASS, SETUP_CHECK, outcome.detail)]
    row = doctor_row(
        SETUP_CHECK,
        scope.plugin,
        WARN,
        SETUP_CHECK,
        outcome.detail,
        fix,
        automatic=outcome.automatic,
    )
    return [row]


def _run_check(
    scope: SectionScope, check_item: DoctorCheck, settings: BaseModel | None, *, profile: str
) -> dict[str, object]:
    ctx = PluginContext(settings=settings, owner=scope.plugin)
    try:
        # Typed as ``object``: provider bodies may return anything at runtime;
        # the shape checks below are the validation.
        outcome: object = check_item.run(ctx)
    except Exception as exc:
        return doctor_row(
            check_item.id,
            scope.plugin,
            FAIL,
            check_item.title,
            f"check raised {type(exc).__name__}: {exc}",
        )
    if not isinstance(outcome, DoctorResult):
        return doctor_row(
            check_item.id,
            scope.plugin,
            FAIL,
            check_item.title,
            f"check returned {type(outcome).__name__}, expected DoctorResult",
        )
    if outcome.id is not None and outcome.id != check_item.id:
        return doctor_row(
            check_item.id,
            scope.plugin,
            FAIL,
            check_item.title,
            f"check returned id {outcome.id!r}, expected {check_item.id!r}",
        )
    ok: object = outcome.ok  # unvalidated too: an id passed first, positionally, lands here
    if not isinstance(ok, bool):
        return doctor_row(
            check_item.id,
            scope.plugin,
            FAIL,
            check_item.title,
            f"check returned ok of type {type(ok).__name__}, expected bool"
            " (DoctorResult takes ok first; id is keyword-only)",
        )
    returned: object = outcome.detail  # an unvalidated dataclass field: may be anything
    if not isinstance(returned, str | None):
        return doctor_row(
            check_item.id,
            scope.plugin,
            FAIL,
            check_item.title,
            f"check returned detail of type {type(returned).__name__}, expected str",
        )
    fix = None
    # A passing row drops its fix, but an automatic one is still validated.
    if outcome.fix and (not outcome.ok or outcome.warn or outcome.automatic):
        try:
            fix = command_argv(outcome.fix, profile=profile)
        except ValueError as exc:
            detail = f"check returned an unparsable fix: {exc}"
            return doctor_row(check_item.id, scope.plugin, FAIL, check_item.title, detail)
    if outcome.automatic and (fix is None or placeholders(fix)):
        needs = f"that needs {', '.join(placeholders(fix))}" if fix else "but no fix"
        detail = f"check {check_item.id} declares an automatic fix {needs}"
        return doctor_row(check_item.id, scope.plugin, FAIL, check_item.title, detail)
    title = check_item.title
    if outcome.ok and not outcome.warn:
        return doctor_row(check_item.id, scope.plugin, PASS, title, outcome.detail or "OK")
    status = FAIL if not outcome.ok else WARN
    detail = outcome.detail or ""
    return doctor_row(
        check_item.id, scope.plugin, status, title, detail, fix, automatic=outcome.automatic
    )


def _contract_rows(
    result: CompositionResult, profile: str, plugins: frozenset[str] | None
) -> list[dict[str, object]]:
    """Doctor's rows about contracts: one per provider offer, owner problem and bad ranking."""
    if not any(plugin.owns_contracts or plugin.fill_owners for plugin in result.plugins):
        return []
    # Imported here: ``untaped.sdk.contracts`` stays out of a composition without contracts.
    from untaped.runtime.contracts.registry import doctor_rows  # noqa: PLC0415

    return [
        doctor_row(
            row.check,
            row.plugin,
            row.status,
            row.title,
            row.detail,
            None
            if row.fix is None or row.status == PASS
            else command_argv(row.fix, profile=profile),
        )
        for row in doctor_rows(plugins)
    ]


def _quarantine_row(record: QuarantineRecord) -> dict[str, object]:
    origin = f"distribution {record.distribution}"
    if record.entry_point:
        origin = f"{origin}, entry point {record.entry_point}"
    return doctor_row("quarantine", record.name, FAIL, record.reason, f"{record.detail} [{origin}]")
