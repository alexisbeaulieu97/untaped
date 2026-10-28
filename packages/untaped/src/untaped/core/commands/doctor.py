"""Root ``untaped doctor`` command group: ``Doctor`` on core's plugin class.

``untaped doctor`` (``Doctor.runs``, :class:`Check`) runs core's plus
every composed plugin's health checks OFFLINE — config-file reads plus
in-process model validation only, never network I/O. ``--online`` adds the checks
plugins contribute with ``DoctorCheck(online=True)``, which contact the
configured services. A check's ``DoctorResult.fix`` becomes the row's
``fix``: a complete argv (``--profile`` first) to run after ``untaped``;
the human view shows it under its row as the command line to type. A row's
``automatic`` says the fix is safe to run unattended (see ``DoctorResult``).
Each row is isolated: invalid settings for one plugin surface as failed
rows while every other row still runs.
Quarantine records render as failed rows (nonzero exit). Core's own checks
(config file permissions, unknown keys, the ``pass`` store) are
``DoctorCheck`` attributes on ``Core`` (:mod:`untaped.core.doctor_checks`),
run like any plugin's. A ``warn`` row does not fail the run: a config file
other users can read, profile keys no settings model declares, renamed,
retired, deleted or deprecated keys
in any profile (fixed by ``config migrate``, except deprecated settings),
installed skills an older untaped wrote, someone edited or no plugin ships,
and a plugin check that returns ``DoctorResult(..., warn=True)``. ``doctor
fix`` (:class:`Fix`, driving :mod:`untaped.core.commands.fix`) runs every
automatic fix the checks name.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Generator, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, ValidationError

from untaped.core.commands import records
from untaped.core.commands._render import emit_check_list, emit_isolated
from untaped.core.commands.skills import composed_skills
from untaped.core.settings import NAME, CoreSettings
from untaped.core.skills import SkillState, local_project_root, outdated_skills
from untaped.runtime.config_file import read_config_dict, read_state_dict
from untaped.runtime.profile_resolver import (
    DEFAULT_PROFILE,
    current_pick,
    fix_target,
    flag_pick,
    picked_names,
    selected_profile,
)
from untaped.runtime.registry import (
    CompositionResult,
    PluginSpec,
    QuarantineRecord,
    owns_contracts,
    run_deferred_factory,
    section_settings,
    state_declarations,
    state_model,
)
from untaped.runtime.run import child_run
from untaped.runtime.settings import (
    HttpSettings,
    active_settings_layout,
    check_settings_field,
    profile_section_models,
    resolve_config_path,
    resolve_state_path,
    resolve_with_overlay,
    retired_env_vars,
    section_stabilities,
)
from untaped.runtime.sink import Diagnostics
from untaped.runtime.theme import UiSettings, check_declared_tokens, resolve_theme
from untaped.sdk._run import SettingsOverlay
from untaped.sdk.errors import ConfigError, Failed, UntapedError, first_validation_error
from untaped.sdk.fields import Confirm, Output, table_row
from untaped.sdk.health._setup_status import SETUP_CHECK, setup_result
from untaped.sdk.health.deprecated_keys import scan_keys
from untaped.sdk.health.doctor import DoctorCheck, DoctorResult, PluginContext
from untaped.sdk.health.marks import section_marks
from untaped.sdk.marks import owner, removal_text
from untaped.sdk.network.http import resolve_verify
from untaped.sdk.plugin import Command, Group
from untaped.sdk.storage.state import ProjectStateRows, StateRows
from untaped.sdk.words import (
    command_argv,
    command_line,
    plural,
    removal_phrase,
    run_hint,
    shown_path,
    size_text,
    split_profile,
    summary,
)

if TYPE_CHECKING:
    from untaped.sdk.projects import Project
    from untaped.sdk.storage._refs import Reference, RefHolder
    from untaped.sdk.storage.store import Layout, Store

_diag = Diagnostics()

_PASS = "pass"
_FAIL = "fail"
_WARN = "warn"
_STATUSES = (_PASS, _WARN, _FAIL)
_PLACEHOLDER = re.compile(r"<[A-Z][A-Z0-9_]*>")


@dataclass(frozen=True)
class _SectionScope:
    """One section's health scope: models plus the check bodies owning it."""

    plugin: str
    section: str
    settings_model: type[BaseModel] | None
    state_model: type[BaseModel] | None
    checks: tuple[DoctorCheck, ...]
    rows: tuple[StateRows[Any] | ProjectStateRows[Any], ...] = ()
    """The section's typed rows, each read by its own rules."""


_ONLINE = "Also contact each configured service (tokens, URLs, TLS)."


class Check(Command[CoreSettings]):
    """Run every health check and report one row per check.

    Examples:
        $ untaped doctor
        $ untaped doctor --online --format json
    """

    repairs = True
    online: bool = Field(False, description=_ONLINE)
    output: Output[records.DoctorCheck]

    def run(self) -> Iterator[records.DoctorCheck]:
        # Lazy: bootstrap is the CLI's root and imports this module.
        from untaped.runtime.bootstrap import composition  # noqa: PLC0415

        rows = collect_doctor_rows(composition(), online=self.online)
        if (yield from report_check_rows(rows, op="doctor", output=self.output)):
            raise Failed("a doctor check failed")


class Fix(Command[CoreSettings]):
    """Run every automatic fix doctor found, after one confirmation.

    No automatic fix asks for or prints a token, so `--yes` passes none.
    A keychain may show an unlock prompt on the user's screen; a locked
    `pass` store exits 5 with a hint naming the unlock command. A `skipped`
    row's `fix` is the argv to run after `untaped`: a `<NAME>` token in it
    is a value to ask for, and a fix that asks for or reveals a token
    (`auth login`, `config set ….token --prompt`) is the user's to run.

    Examples:
        $ untaped doctor fix --dry-run
        $ untaped doctor fix --yes
    """

    repairs = True
    online: bool = Field(False, description=_ONLINE)
    confirm: Confirm
    output: Output[records.FixOutcome]

    def run(self) -> Iterator[records.FixOutcome]:
        # Lazy: bootstrap is the CLI's root and imports this module; fix imports doctor.
        from untaped.core.commands.fix import run_fixes  # noqa: PLC0415
        from untaped.runtime.bootstrap import composition, root_command  # noqa: PLC0415

        return run_fixes(
            composition(),
            online=self.online,
            confirm=self.confirm,
            shows_itself=_shows_itself(self.output),
            columns=self.output.requested,
            builtin_for=root_command,
        )


class Doctor(Group):
    """Check the health of untaped and every composed plugin."""

    runs = Check
    commands = (Fix,)


def _shows_itself(output: Output[Any]) -> bool:
    """Whether the command prints its own human view: a ``table`` not listing its columns.

    Rows print only after ``run`` ends, and the human view puts the summary
    and hint after them, so doctor draws its table itself (with
    ``--columns``, a table whose ``fix`` is the line to type).
    """
    return output.format == "table" and output.requested != ["?"]


def report_check_rows(
    rows: list[dict[str, object]], *, op: str, output: Output[Any]
) -> Generator[records.DoctorCheck, None, bool]:
    """Report doctor rows and an ``op`` footer; return whether a check failed.

    Under a ``table`` the command prints the checklist itself (with
    ``--columns``, the table whose ``fix`` is the command line to type);
    otherwise it yields the ``untaped.doctor_check`` records for ``output``.
    """
    if _shows_itself(output):
        _show_check_rows(rows, columns=output.requested)
    else:
        yield from (records.DoctorCheck.model_validate(row) for row in rows)
    return _check_footer(rows, op=op)


def _show_check_rows(rows: list[dict[str, object]], *, columns: list[str] | None) -> None:
    """Doctor rows as a person reads them: the checklist, or with ``columns`` a table."""
    if columns is None:
        emit_check_list(rows, run_line=run_line)
        _diag.info("")
    else:
        # A table shows ``fix`` as the line to type, so it is a view, not the record.
        shown = [
            table_row(records.DoctorCheck.model_validate(row), _table_row(row)) for row in rows
        ]
        emit_isolated(shown, fmt="table", columns=columns)


def _check_footer(rows: list[dict[str, object]], *, op: str) -> bool:
    """Print the ``op`` summary and the ``doctor fix`` hint; return whether a check failed."""
    counts = {status: sum(row["status"] == status for row in rows) for status in _STATUSES}
    _diag.info(summary(op, counts))
    fix_hint = _fix_hint(rows)
    if fix_hint:
        _diag.hint(fix_hint)
    return bool(counts[_FAIL])


def _fix_hint(rows: list[dict[str, object]]) -> str | None:
    """``run `untaped doctor fix` …`` (a hint's body) when an automatic fix exists, else ``None``.

    Fixes are counted as ``doctor fix`` groups them; the command reads the
    run's whole pick, as doctor did.
    """
    fixes = {argv: is_automatic(covered) for argv, covered in group_fixes(rows).items()}
    automatic = [argv for argv, auto in fixes.items() if auto]
    if not automatic:
        return None
    command = run_line(command_argv("doctor fix", profile=selected_profile()))
    text = (
        f"{run_hint(command)} to apply {plural(len(automatic), 'automatic fix', 'automatic fixes')}"
    )
    manual = len(fixes) - len(automatic)
    if manual:
        text = f"{text}; {plural(manual, 'fix', 'fixes')} {'needs' if manual == 1 else 'need'} you"
    return text


def _table_row(row: dict[str, object]) -> dict[str, object]:
    fix = row.get("fix")
    return {**row, "fix": run_line(fix) if isinstance(fix, list) and fix else ""}


def run_line(argv: list[str]) -> str:
    """A fix or step argv as the ``untaped …`` line a person types.

    ``--profile default`` goes without saying when the run picks no profile
    and no ``--profile`` was given: the bare line then reads and writes the
    base too.
    """
    named, rest = split_profile(argv)
    if named == DEFAULT_PROFILE and not flag_pick() and not picked_names():
        argv = rest
    return command_line(shlex.join(argv))


def _row(
    check: str,
    plugin: str,
    status: str,
    title: str,
    detail: str,
    fix: list[str] | None = None,
    *,
    automatic: bool = False,
) -> dict[str, object]:
    return {
        "check": check,
        "plugin": plugin,
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


def _scopes(result: CompositionResult) -> list[_SectionScope]:
    # The root's parts get a row each (_core_row), so its checks get no settings: one
    # reads its part with load_core_part.
    return [
        _plugin_scope(
            registered.spec,
            settings=not registered.root,
            held=tuple(block for _, block in result.mount.held(registered.spec.name, "state-rows")),
        )
        for registered in result.plugins
    ]


def _plugin_scope(
    spec: PluginSpec,
    *,
    settings: bool = True,
    held: tuple[StateRows[Any] | ProjectStateRows[Any], ...] = (),
) -> _SectionScope:
    """``spec``'s scope; without ``settings`` no section row. ``held``: its class's state rows."""
    return _SectionScope(
        plugin=spec.name,
        section=spec.name,
        settings_model=section_settings(spec) if settings else None,
        state_model=state_model(spec),
        checks=tuple(spec.doctor_checks),
        rows=(
            *(
                each
                for each in state_declarations(spec)
                if isinstance(each, StateRows | ProjectStateRows)
            ),
            *held,
        ),
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
    state, state_file_row = _state_file_row()
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
        rows.append(_profile_row(settings_error))
    for part in CoreSettings.model_fields:
        rows.append(_core_row(part, effective, settings_error))
    contexts: list[tuple[_SectionScope, BaseModel | None]] = []
    scopes = _scopes(result)
    profile = fix_target()
    for scope in scopes:
        settings = _settings_row(scope, effective, settings_error, rows)
        contexts.append((scope, settings))
        rows.extend(_setup_rows(scope, settings, profile=profile))
        if scope.state_model is not None or scope.rows:
            rows.append(_state_row(scope, scope.state_model, state))
    rows.extend(_check_rows(contexts, online=online, plugins=plugins, profile=profile))
    rows.extend(_contract_rows(result, profile, plugins))
    rows.append(_skills_row(result))
    if plugins is None:
        rows.append(_references_row(result))
        rows.append(_stores_row(result))
        rows.append(_caches_row(result))
        rows.append(_plugin_folders_row(result))
    for record in result.quarantine:
        rows.append(_quarantine_row(record))
    for registered in result.plugins:
        selected = plugins is None or registered.spec.name in plugins
        if registered.spec.app_factory is None or not selected:
            continue
        built = run_deferred_factory(registered)
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
        or (row["check"] == "deprecated-keys" and row["status"] != _PASS)
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
    spec = next(
        (registered.spec for registered in result.plugins if registered.spec.name == plugin),
        None,
    )
    if spec is None:
        raise ConfigError(f"no composed plugin named {plugin!r} to check")
    scope = _plugin_scope(spec)
    overlay = SettingsOverlay(profile, scope.section, dict(values))
    with child_run(profiles=profile, overlay=overlay):
        settings, error = None, None
        if scope.settings_model is not None:
            settings, error = _validate_section(scope, scope.settings_model, *_overlaid_effective())
        if error is not None:
            return [_row("settings", scope.plugin, _FAIL, "validate settings", error)]
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
        return _row("deprecated-keys", NAME, _PASS, title, "no deprecated keys")
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
        fix = command_argv("config migrate", profile=fix_target())
    return _row("deprecated-keys", NAME, _WARN, title, "; ".join(parts), fix, automatic=True)


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
        return _row("skills", NAME, _PASS, title, "no outdated skills")
    parts = [f"{item.state.value} skill: {item.path}" for item in stale]
    fix = None
    if any(item.state is SkillState.outdated for item in stale):
        fix = command_argv("skills update", profile=fix_target())
    if any(item.state is SkillState.edited for item in stale):
        parts.append(
            "edited stubs are never rewritten by a run: keep them, or replace one with"
            " `untaped skills update --force NAME`"
        )
    if any(item.state is SkillState.orphaned for item in stale):
        parts.append(
            "orphaned skills are shipped by no plugin installed here: install the plugin"
            " that ships one, or remove it with `untaped skills remove NAME`"
        )
    detail = "; ".join(parts)
    return _row("skills", NAME, _WARN, title, detail, fix, automatic=True)


def _stores_row(result: CompositionResult) -> dict[str, object]:
    """Each composed store's layouts on disk and who holds each.

    Green while every layout on disk is held or will go at the next complete
    fill; yellow for one the cleanup cannot remove, with the reason.
    """
    from untaped.sdk.storage._refs import read_references  # noqa: PLC0415 - runs only in doctor
    from untaped.sdk.storage.store import Store, survey  # noqa: PLC0415

    title = "stores keep one layout each, or every layout is held"
    stores = [
        store
        for registered in result.plugins
        for store in registered.spec.stores
        if isinstance(store, Store)  # an item store keeps no layouts
    ]
    if not stores:
        return _row("stores", NAME, _PASS, title, "no plugin declares a store")
    references = read_references()
    parts: list[str] = []
    reasons: set[str] = set()
    for store in stores:
        try:
            path, layouts = store.path(), survey(store, references)
        except Exception as exc:
            parts.append(f"{store.qualified}: could not find its files: {exc}")
            reasons.add("files")
            continue
        texts: list[str] = []
        if not any(layout.own for layout in layouts):
            texts.append(f"layout {store.layout} at {shown_path(path)}, not created yet")
        for layout in layouts:
            text, warns = _layout_text(store, layout, references)
            texts.append(text)
            reasons |= warns
        parts.append(f"{store.qualified}: {'; '.join(texts)}")
    if not reasons:
        return _row("stores", NAME, _PASS, title, "; ".join(parts))
    return _row("stores", NAME, _WARN, title, "; ".join(parts), _stores_fix(reasons))


def _caches_row(result: CompositionResult) -> dict[str, object]:
    """What doctor notes about caches: always a pass, since every cache refills.

    A cache's keys holding only another version's value. The caches of a
    plugin not installed here are in its folder's row (:func:`_plugin_folders_row`).
    """
    from untaped.sdk.storage.cache import outdated_keys  # noqa: PLC0415 - runs only in doctor

    title = "no caches of another version"
    parts: list[str] = []
    for spec in (registered.spec for registered in result.plugins):
        for cache in spec.caches:
            try:
                outdated = outdated_keys(cache)
            except Exception as exc:  # the plugin's version= or model= code runs here
                parts.append(f"{cache.qualified}: could not check its files: {exc}")
                continue
            if outdated:
                keys = plural(outdated, "key", "keys")
                parts.append(
                    f"{cache.qualified}: {keys} filled by another version, refilled when due"
                )
    return _row("caches", NAME, _PASS, title, "; ".join(parts) or "nothing to note")


def _plugin_folders_row(result: CompositionResult) -> dict[str, object]:
    """Each ``plugins/<name>/`` folder of a plugin not installed here, offered to ``setup prune``.

    It warns while one could go (no live reference names its plugin): it
    goes whole, state included, so only a person removes it. One that stays
    (a symlink, a plugin a live reference names, any reference unreadable)
    is listed with why.
    """
    from untaped.core.commands.setup_prune import leftover_folders  # noqa: PLC0415
    from untaped.sdk.storage._fs import dir_bytes  # noqa: PLC0415 - runs only in doctor
    from untaped.sdk.storage._refs import read_references  # noqa: PLC0415

    title = "no folders of plugins not installed here"
    leftovers = leftover_folders(result, read_references())
    if not leftovers:
        return _row("plugin-folders", NAME, _PASS, title, "nothing to note")
    parts = []
    for leftover in leftovers:
        text = f"{shown_path(leftover.path)}: plugin {leftover.path.name!r} isn't installed here"
        if leftover.kept is not None:
            parts.append(f"{text}; kept: {leftover.kept}")
        else:
            size = size_text(dir_bytes(leftover.path))
            parts.append(f"{text} and nothing here uses it ({size}); it goes whole, state included")
    if all(leftover.kept is not None for leftover in leftovers):
        return _row("plugin-folders", NAME, _PASS, title, "; ".join(parts))
    return _row("plugin-folders", NAME, _WARN, title, "; ".join(parts), _prune_fix())


def _prune_fix() -> list[str]:
    """``setup prune``'s argv, spelled from its class.

    Never automatic: what it removes may be a disk not mounted now, or a
    plugin's state, so a person decides.
    """
    from untaped.core.commands.setup_prune import Prune  # noqa: PLC0415 - setup imports doctor

    return command_argv(Prune.spelled(), profile=fix_target())


def _register_fix() -> list[str]:
    """``setup register``'s argv, spelled from its class; never automatic (a deliberate step)."""
    from untaped.core.commands.register import Register  # noqa: PLC0415 - setup imports doctor

    return command_argv(Register.spelled(), profile=fix_target())


def _layout_text(
    store: Store, layout: Layout, references: Sequence[Reference]
) -> tuple[str, set[str]]:
    """One layout as the ``stores`` row says it, and the codes of what it warns about.

    A damaged in-use lock is said even when ``keep`` or unknown holders are
    the reason the cleanup keeps the layout.
    """
    from untaped.sdk.storage._fs import dir_bytes  # noqa: PLC0415 - runs only in doctor
    from untaped.sdk.storage._refs import this_install  # noqa: PLC0415
    from untaped.sdk.storage.store import _damaged, kept_reason  # noqa: PLC0415

    size = size_text(dir_bytes(layout.path))
    text = f"layout {layout.layout} at {shown_path(layout.path)} ({size})"
    reason = kept_reason(store, layout, references)
    me = this_install()
    if layout.holders:
        text += ", held by " + ", ".join(
            "this install" if me and ref.holder == me.holder else ref.describe()
            for ref in layout.holders
        )
    elif not layout.own and reason is not None and not reason.unheld:
        text += ", holders unknown"
    elif not layout.own:
        text += ", held by nobody"
        if not reason:
            text += " (removed at the next complete fill)"
    found = [reason] if reason else []
    damaged = _damaged(layout)
    if damaged is not None and reason is not None and reason.code != "damaged":
        found.append(damaged)
    if found:
        text += ": " + ", and ".join(kept.text for kept in found)
    return text, {kept.code for kept in found if kept.warns}


def _stores_fix(reasons: set[str]) -> list[str] | None:
    """The ``stores`` row's fix for what keeps its layouts: register, or none.

    Never automatic: registering is a deliberate step.
    """
    if "unregistered" in reasons:
        return _register_fix()
    return None


def _references_row(result: CompositionResult) -> dict[str, object]:
    """Every reference in the home, with its state, and whether this install is registered.

    Installs hold store layouts; projects hold items. A missing project
    warns; an unreadable one (its drive or share away) is listed, not warned.
    An install that is not reachable warns, its fix ``setup prune``, unless
    this install needs registering first.
    """
    from untaped.core.commands.setup_prune import dead_references  # noqa: PLC0415
    from untaped.sdk.storage._refs import (  # noqa: PLC0415 - runs only in doctor
        REFS,
        environment,
        install_holder,
        read_references,
    )

    title = "references"
    references = read_references()
    env = environment()
    mine = install_holder(env) if env is not None else None
    described = [_reference_text(ref, mine) for ref in references]
    parts = [text for text, _ in described]
    warn = any(warns for _, warns in described)
    registered = any(ref.opaque is None and ref.holder == mine for ref in references)
    # a dead reference is removed by a person: another install's registration is never a run's
    fix = _prune_fix() if dead_references(references) else None
    if env is None:
        parts.append("this run is from a source tree: it cannot be registered")
    elif not registered and not any(ref.file == REFS.place for ref in references):
        if _other_layouts(result, references):
            warn = True
            parts.insert(0, "this install is not registered: nothing cleans up old store layouts")
            fix = _register_fix()
        else:
            parts.insert(0, "this install: not registered")
    detail = "; ".join(parts) or "no install is registered"
    # Never automatic: a lasting registration is a deliberate step (a one-off `uvx` run's
    # environment would otherwise pin layouts until its installer's cache is cleared).
    return _row("references", NAME, _WARN if warn else _PASS, title, detail, fix)


def _reference_text(ref: Reference, mine: RefHolder | None) -> tuple[str, bool]:
    """How the ``references`` row names one reference, and whether it warns."""
    from untaped.sdk.storage._refs import (  # noqa: PLC0415 - runs only in doctor
        INSTALL,
        PROJECT,
        REFS,
    )

    if ref.opaque is not None:
        if "newer" in ref.opaque:
            advice = "; upgrade this install to prune"
        elif ref.file == REFS.place:
            advice = ", so nothing is pruned; check its permissions"
        else:
            advice = ", so nothing is pruned; delete it"
        return f"{shown_path(ref.file)}: {ref.opaque}{advice}", True
    if ref.kind == PROJECT:
        return _project_text(ref)
    text = ref.describe()
    if ref.kind != INSTALL and ref.plugin:
        text += f" (plugin {ref.plugin})"
    if ref.holder == mine:
        text += " (this install)"
    if not ref.live:
        return f"{text}: not reachable", True
    if ref.pre_upgrade:
        text += ": reference from before an upgrade; run any command from that install"
        text += " to refresh it"
    return text, False


def _project_text(ref: Reference) -> tuple[str, bool]:
    """How the ``references`` row names a project, and whether it warns (missing, repair due)."""
    from untaped.sdk.projects import anchor_state  # noqa: PLC0415 - runs only in doctor

    where = shown_path(ref.anchor) if ref.anchor is not None else "?"
    state = anchor_state(ref)
    if state == "missing":
        return f"{ref.describe()}: {where} is gone; it keeps what it holds until removed", True
    if state == "unreadable":
        return f"{ref.describe()}: {where} can't be read now (a drive or share away?)", False
    if ref.moved_from:
        was = ", ".join(shown_path(path) for path in ref.moved_from)
        return (
            f"{ref.describe()} at {where}: moved from {was}, and a plugin's "
            "repair is still pending; the next command that changes the project retries it",
            True,
        )
    return f"{ref.describe()} at {where}", False


def _other_layouts(result: CompositionResult, references: Sequence[Reference]) -> bool:
    """Whether any composed store has a layout on disk besides its own."""
    from untaped.sdk.storage.store import Store, survey  # noqa: PLC0415 - runs only in doctor

    for registered in result.plugins:
        for store in registered.spec.stores:
            if not isinstance(store, Store):
                continue
            try:
                layouts = survey(store, references)
            except Exception:
                continue
            if any(not layout.own for layout in layouts):
                return True
    return False


def _config_row() -> tuple[dict[str, Any] | None, dict[str, object]]:
    path = resolve_config_path()
    try:
        raw = read_config_dict(path)
    except ConfigError as exc:
        return None, _row("config", NAME, _FAIL, "load config file", str(exc))
    return raw, _row("config", NAME, _PASS, "load config file", str(path))


def _state_file_row() -> tuple[tuple[dict[str, Any], Path] | None, dict[str, object]]:
    """Load ``state.yml``; return ``((raw, path), row)`` or ``(None, failed row)``."""
    title = "load state file"
    try:
        path = resolve_state_path()
        raw = read_state_dict(path)
    except ConfigError as exc:
        return None, _row("config", NAME, _FAIL, title, str(exc))
    return (raw, path), _row("config", NAME, _PASS, title, str(path))


def _profile_row(settings_error: str | None) -> dict[str, object]:
    """Each picked profile (flag or env) must exist."""
    title = "resolve picked profiles"
    if settings_error is not None:
        return _row("profile", NAME, _FAIL, title, settings_error)
    picks = current_pick()
    detail = ", ".join(f"{pick.name} (via {pick.source})" for pick in picks)
    return _row("profile", NAME, _PASS, title, detail or "default (via fallback)")


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
        return _row("settings", NAME, _FAIL, title, settings_error or "config unreadable")
    section = effective.get(NAME)
    if section is not None and not isinstance(section, dict):
        return _row("settings", NAME, _FAIL, title, f"section {NAME!r} must be a mapping")
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
        return _row("settings", NAME, _FAIL, title, str(exc))
    return _row("settings", NAME, _PASS, title, "settings OK")


def _state_row(
    scope: _SectionScope,
    state_model: type[BaseModel] | None,
    state: tuple[dict[str, Any], Path] | None,
) -> dict[str, object]:
    """Validate a plugin's state section in ``state.yml`` (profile-independent).

    Typed rows are read by their own rules (a damaged row fails with its
    fix); inside a project, so are that project's rows.
    """
    title = "validate state"
    if state is None:
        return _row("state", scope.plugin, _FAIL, title, "state file could not be read")
    state_raw, source = state
    node = state_raw.get(scope.section)
    if node is not None and not isinstance(node, dict):
        detail = f"state section {scope.section!r} in {source} must be a mapping"
        return _row("state", scope.plugin, _FAIL, title, detail)
    owned = {rows.name for rows in scope.rows}
    try:
        if state_model is not None:  # None: only a project's rows, checked below
            state_model.model_validate({k: v for k, v in (node or {}).items() if k not in owned})
    except ValidationError as exc:
        detail = f"invalid {scope.section} state in {source}: {first_validation_error(exc)}"
        return _row("state", scope.plugin, _FAIL, title, detail)
    try:
        counts = _row_counts(scope.rows)
    except ConfigError as exc:
        detail = str(exc) if exc.hint is None else f"{exc}; {exc.hint}"
        return _row("state", scope.plugin, _FAIL, title, detail)
    if node is None and not counts:
        return _row("state", scope.plugin, _PASS, title, "no state")
    return _row("state", scope.plugin, _PASS, title, "; ".join(["state OK", *counts]))


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
    scope: _SectionScope,
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
    scope: _SectionScope,
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
        rows.append(_row("settings", scope.plugin, _FAIL, "validate settings", error))
    else:
        rows.append(_row("settings", scope.plugin, _PASS, "validate settings", "settings OK"))
    return settings


def _setup_rows(
    scope: _SectionScope, settings: BaseModel | None, *, profile: str
) -> list[dict[str, object]]:
    """Core's ``setup`` row for a plugin that marks what it needs; none for any other."""
    marks = section_marks(scope.section, scope.settings_model)
    if marks is None:
        return []
    outcome = setup_result(settings, marks, section=scope.section)
    fix = None if outcome.fix is None else command_argv(outcome.fix, profile=profile)
    if outcome.ok and not outcome.warn:
        return [_row(SETUP_CHECK, scope.plugin, _PASS, SETUP_CHECK, outcome.detail)]
    row = _row(
        SETUP_CHECK,
        scope.plugin,
        _WARN,
        SETUP_CHECK,
        outcome.detail,
        fix,
        automatic=outcome.automatic,
    )
    return [row]


def _run_check(
    scope: _SectionScope, check_item: DoctorCheck, settings: BaseModel | None, *, profile: str
) -> dict[str, object]:
    ctx = PluginContext(settings=settings, owner=scope.plugin)
    try:
        # Typed as ``object``: provider bodies may return anything at runtime;
        # the shape checks below are the validation.
        outcome: object = check_item.run(ctx)
    except Exception as exc:
        return _row(
            check_item.id,
            scope.plugin,
            _FAIL,
            check_item.title,
            f"check raised {type(exc).__name__}: {exc}",
        )
    if not isinstance(outcome, DoctorResult):
        return _row(
            check_item.id,
            scope.plugin,
            _FAIL,
            check_item.title,
            f"check returned {type(outcome).__name__}, expected DoctorResult",
        )
    if outcome.id is not None and outcome.id != check_item.id:
        return _row(
            check_item.id,
            scope.plugin,
            _FAIL,
            check_item.title,
            f"check returned id {outcome.id!r}, expected {check_item.id!r}",
        )
    ok: object = outcome.ok  # unvalidated too: an 11.x positional id lands here
    if not isinstance(ok, bool):
        return _row(
            check_item.id,
            scope.plugin,
            _FAIL,
            check_item.title,
            f"check returned ok of type {type(ok).__name__}, expected bool"
            " (DoctorResult takes ok first; id is keyword-only)",
        )
    returned: object = outcome.detail  # an unvalidated dataclass field: may be anything
    if not isinstance(returned, str | None):
        return _row(
            check_item.id,
            scope.plugin,
            _FAIL,
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
            return _row(check_item.id, scope.plugin, _FAIL, check_item.title, detail)
    if outcome.automatic and (fix is None or placeholders(fix)):
        needs = f"that needs {', '.join(placeholders(fix))}" if fix else "but no fix"
        detail = f"check {check_item.id} declares an automatic fix {needs}"
        return _row(check_item.id, scope.plugin, _FAIL, check_item.title, detail)
    title = check_item.title
    if outcome.ok and not outcome.warn:
        return _row(check_item.id, scope.plugin, _PASS, title, outcome.detail or "OK")
    status = _FAIL if not outcome.ok else _WARN
    detail = outcome.detail or ""
    return _row(
        check_item.id, scope.plugin, status, title, detail, fix, automatic=outcome.automatic
    )


def _contract_rows(
    result: CompositionResult, profile: str, plugins: frozenset[str] | None
) -> list[dict[str, object]]:
    """Doctor's rows about contracts: one per provider offer, owner problem and bad ranking."""
    specs = [registered.spec for registered in result.plugins]
    if not any(owns_contracts(spec) or spec.provides for spec in specs):
        return []
    # Imported here: ``untaped.sdk.contracts`` stays out of a composition without contracts.
    from untaped.runtime.contracts.registry import doctor_rows  # noqa: PLC0415

    return [
        _row(
            row.check,
            row.plugin,
            row.status,
            row.title,
            row.detail,
            None
            if row.fix is None or row.status == _PASS
            else command_argv(row.fix, profile=profile),
        )
        for row in doctor_rows(plugins)
    ]


def _quarantine_row(record: QuarantineRecord) -> dict[str, object]:
    origin = f"distribution {record.distribution}"
    if record.entry_point:
        origin = f"{origin}, entry point {record.entry_point}"
    return _row("quarantine", record.name, _FAIL, record.reason, f"{record.detail} [{origin}]")


__all__ = [
    "Doctor",
    "collect_doctor_rows",
    "online_probe_rows",
    "report_check_rows",
    "selected_check_rows",
]
