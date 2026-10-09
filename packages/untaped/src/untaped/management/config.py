"""Root ``untaped config …`` command group.

Key resolution is direct: a fully qualified ``section.key``
selects its schema by ``section``. A renamed key resolves to its new name
with a warning and a retired one is rejected; the section's own state fields
raise the "managed by" error, and anything else passes through to the
schema. Bare keys are never implicitly expanded to a plugin section.

All read/write logic (list/get/set/unset/edit) is imported from the existing
config modules; only :class:`RootConfigContext` (the root resolution rule)
is new. There is deliberately no nested ``config doctor`` here: config
diagnostics live at the root ``doctor`` command, where one broken section
cannot block the other rows.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated

from cyclopts import App, Parameter

from untaped.cli import (
    ColumnsOption,
    DryRunOption,
    FormatOption,
    create_app,
    echo,
    emit,
    report_errors,
    writes,
)
from untaped.config.editor import run_config_editor
from untaped.config.models import SettingEntry, SettingOutcome, SettingRow, setting_entry_row
from untaped.config.prompting import resolve_set_value
from untaped.config.repository import SettingsFileRepository
from untaped.config.use_cases import GetSetting, ListAllProfilesSettings, ListSettings
from untaped.config_file import read_config_dict
from untaped.deprecated_keys import NO_KEY_MAPPINGS, KeyMappings, key_mappings, warn_once
from untaped.errors import ConfigError
from untaped.messages import deprecated_message, hint, plural
from untaped.plugins.registry import ApplicationSpec, CompositionResult, settings_model
from untaped.settings import (
    Settings,
    active_settings_layout,
    config_key_warning,
    model_sections,
    resolve_config_path,
)
from untaped.stability import show_deprecated
from untaped.theme import OutputFormat
from untaped.ui import ui_context


@dataclass(frozen=True)
class RootSectionScope:
    """One section's key-resolution scope for the root config command."""

    plugin: str
    """Owning plugin name (the shell name for the shell section)."""

    settings_fields: frozenset[str]
    """User-tunable field names of the section's profile model."""

    state_fields: frozenset[str]
    """Tool-managed field names of the section's state model (never settable)."""

    mappings: KeyMappings = NO_KEY_MAPPINGS
    """The section model's renamed, retired and deprecated keys."""


@dataclass(frozen=True)
class RootConfigContext:
    """Root key-resolution rule (spec §4, root half).

    A fully qualified ``section.key`` resolves against that section's scope:
    a renamed key becomes its new key (with a warning), a retired key and a
    state field are rejected; bare keys and unknown sections pass through
    untouched so the schema reports them.
    """

    sections: Mapping[str, RootSectionScope]

    def resolve_key(self, key: str) -> str:
        """Map a user key to the concrete config key without implicit expansion."""
        first, rest = _split_first(key)
        if rest is None:
            # Bare keys never expand to a plugin section at the root.
            return key
        scope = self.sections.get(first)
        if scope is None:
            return key
        if rest in scope.mappings.readable:
            new = f"{first}.{scope.mappings.readable[rest]}"
            warn_once(_file_warning(first, rest) or deprecated_message(key, new), key=key)
            return new
        if rest in scope.mappings.retired:
            new = f"{first}.{scope.mappings.migratable[rest]}"
            raise ConfigError(
                f"unknown setting: {key!r} (retired; now {new})\n{hint('config migrate')}",
                category="invalid",
            )
        state_first, _ = _split_first(rest)
        if state_first in scope.state_fields:
            raise self._state_error(first, key)
        return key

    def _state_error(self, section: str, key: str) -> ConfigError:
        scope = self.sections[section]
        return ConfigError(
            f"{key!r} is managed by untaped {scope.plugin} and is not a configurable setting",
            category="invalid",
        )


def _file_warning(section: str, old: str) -> str | None:
    """The ``config.yml`` warning for ``old`` (with its migrate hint), if a layered profile has it.

    Reading the file warns about the same key; both share one ``warn_once``
    key, so a command that names an old key the file also has warns once.
    """
    try:
        resolved = active_settings_layout().resolve(read_config_dict())
    except ConfigError:
        return None
    for sections in resolved.uses.values():
        for use in sections.get(section, ()):
            if use.old == old:
                return config_key_warning(use, section=section)
    return None


def _split_first(key: str) -> tuple[str, str | None]:
    first, sep, rest = key.partition(".")
    return first, rest if sep else None


def section_scopes(
    shell: ApplicationSpec, result: CompositionResult
) -> dict[str, RootSectionScope]:
    """Build the root resolution scopes for core, the shell and composed plugins."""
    scopes = {
        section: RootSectionScope(
            plugin=shell.name,
            settings_fields=frozenset(model.model_fields),
            state_fields=frozenset(),
            mappings=key_mappings(model),
        )
        for section, model in model_sections(Settings).items()
    }
    scopes[shell.section] = RootSectionScope(
        plugin=shell.name,
        settings_fields=frozenset(shell.settings.model_fields),
        state_fields=frozenset(shell.state.model_fields if shell.state is not None else ()),
        mappings=key_mappings(shell.settings),
    )
    for registered in result.plugins:
        spec = registered.spec
        scopes[spec.name] = RootSectionScope(
            plugin=spec.name,
            settings_fields=frozenset(settings_model(spec).model_fields),
            state_fields=frozenset(spec.state.model_fields if spec.state is not None else ()),
            mappings=key_mappings(settings_model(spec)),
        )
    return scopes


def build_root_config_app(*, shell: ApplicationSpec, result: CompositionResult) -> App:
    """Return the root ``config`` command group for one composition."""
    ctx = RootConfigContext(sections=section_scopes(shell, result))
    app = create_app(name="config", help="Inspect and modify ``~/.untaped/config.yml``.")

    @app.command(name="list")
    def list_command(
        *,
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
        show_secrets: Annotated[
            bool,
            Parameter(
                name="--show-secrets", negative="", help="Reveal secret values instead of `***`."
            ),
        ] = False,
        all_profiles: Annotated[
            bool,
            Parameter(
                name="--all-profiles",
                negative="",
                help="Show one row per (profile, key) instead of the resolved view.",
            ),
        ] = False,
    ) -> None:
        """List settings for every composed section (resolved effective values)."""
        _list(fmt=fmt, columns=columns, show_secrets=show_secrets, all_profiles=all_profiles)

    @app.command(name="get")
    def get_command(
        key: Annotated[str, Parameter(help="Fully qualified setting key (section.key).")],
        /,
        *,
        fmt: FormatOption = "raw",
        show_secrets: Annotated[
            bool,
            Parameter(
                name="--show-secrets", negative="", help="Reveal secret values instead of `***`."
            ),
        ] = False,
    ) -> None:
        """Print one effective setting value (mappings and lists as JSON in raw/table)."""
        _get(ctx, key, fmt=fmt, show_secrets=show_secrets)

    @app.command(name="set")
    @writes
    def set_command(
        key: Annotated[str, Parameter(help="Fully qualified setting key (section.key).")],
        value: Annotated[str | None, Parameter(help="New value (validated for its type).")] = None,
        /,
        *,
        stdin: Annotated[
            bool, Parameter(name="--stdin", negative="", help="Read the value from stdin.")
        ] = False,
        prompt: Annotated[
            bool,
            Parameter(
                name="--prompt", negative="", help="Prompt for the value using the setting type."
            ),
        ] = False,
        dry_run: DryRunOption = False,
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """Persist ``section.key = value`` in the active profile (or the root ``--profile``)."""
        _set(
            ctx,
            key,
            value,
            stdin=stdin,
            prompt=prompt,
            dry_run=dry_run,
            fmt=fmt,
            columns=columns,
        )

    @app.command(name="unset")
    @writes
    def unset_command(
        key: Annotated[str, Parameter(help="Fully qualified setting key (section.key).")],
        /,
        *,
        dry_run: DryRunOption = False,
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """Remove ``section.key`` from the active profile (or the root ``--profile``)."""
        _unset(ctx, key, dry_run=dry_run, fmt=fmt, columns=columns)

    @app.command(name="migrate")
    @writes
    def migrate_command(
        *,
        dry_run: DryRunOption = False,
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """Rename deprecated keys in every profile of config.yml.

        A key also set under its new name (or a closer old name) in the same
        profile is dropped. Environment variables and ``state.yml`` are not
        changed.
        """
        _migrate(dry_run=dry_run, fmt=fmt, columns=columns)

    @app.command(name="edit")
    def edit_command() -> None:
        """Open ``~/.untaped/config.yml`` in $VISUAL/$EDITOR and re-validate on save."""
        run_config_editor()

    return app


def _list(
    *,
    fmt: OutputFormat,
    columns: list[str] | None,
    show_secrets: bool,
    all_profiles: bool,
) -> None:
    with report_errors():
        repo = SettingsFileRepository()
        if all_profiles:
            entries = ListAllProfilesSettings(repo)(reveal_secrets=show_secrets)
        else:
            list_settings = ListSettings(repo)
            entries = list_settings(reveal_secrets=show_secrets)
            ui = ui_context(strict=False)
            for error in list_settings.errors.values():
                # The error already names the section (or env var) and file.
                ui.message("warning", f"{error} (its keys show unvalidated values)")
        human = fmt in ("table", "raw")
        if fmt != "table" or columns == ["?"]:
            rows = [setting_entry_row(e, human=human) for e in entries]
            emit(rows, fmt=fmt, columns=columns, kind="untaped.setting")
            return
        _emit_split_tables(entries, columns=columns)


_STABLE_COLUMNS = tuple(column for column in SettingRow.table_columns if column != "note")


def _emit_split_tables(entries: list[SettingEntry], *, columns: list[str] | None) -> None:
    """Print the stable settings, then the experimental ones, then the deprecated ones.

    A deprecated setting at its default is left out unless ``--deprecated``;
    set in a file or the environment it is listed, as is every setting read
    through an old spelling (whatever its stability, so the deprecated
    spelling is never missed). Each table has its own columns, so
    ``ui.hide_empty_columns`` and ``--columns`` apply per table, and only the
    deprecated table carries ``note``.
    """
    stable: list[SettingEntry] = []
    experimental: list[SettingEntry] = []
    deprecated: list[SettingEntry] = []
    for entry in entries:
        if entry.note is not None or (entry.stability == "deprecated" and _shown(entry)):
            deprecated.append(entry)
        elif entry.stability == "experimental":
            experimental.append(entry)
        elif entry.stability == "stable":
            stable.append(entry)
    tables = [
        (None, stable, _STABLE_COLUMNS),
        ("Experimental", experimental, _STABLE_COLUMNS),
        ("Deprecated", deprecated, SettingRow.table_columns),
    ]
    present = [table for table in tables if table[1]] or tables[:1]
    for index, (heading, group, table_columns) in enumerate(present):
        if index:
            echo()
        if heading is not None:
            echo(heading)
        emit(
            [setting_entry_row(entry, human=True) for entry in group],
            fmt="table",
            columns=columns,
            kind="untaped.setting",
            table_columns=table_columns,
        )


def _shown(entry: SettingEntry) -> bool:
    """Whether a deprecated setting is listed: set somewhere, or ``--deprecated``."""
    return entry.source.kind in ("profile", "env") or show_deprecated()


def _get(ctx: RootConfigContext, key: str, *, fmt: OutputFormat, show_secrets: bool) -> None:
    with report_errors():
        resolved = ctx.resolve_key(key)
        entry = GetSetting(SettingsFileRepository())(resolved, reveal_secrets=show_secrets)
        # A detail view hides empty fields; naming them keeps an unset `—` shown.
        columns = {"raw": ["value"], "table": ["key", "value", "default", "source"]}.get(fmt)
        emit(
            setting_entry_row(entry, human=fmt in ("table", "raw")),
            fmt=fmt,
            columns=columns,
            kind="untaped.setting",
        )


def _set(
    ctx: RootConfigContext,
    key: str,
    value: str | None,
    *,
    stdin: bool,
    prompt: bool,
    dry_run: bool,
    fmt: OutputFormat,
    columns: list[str] | None,
) -> None:
    with report_errors():
        repo = SettingsFileRepository()
        resolved = ctx.resolve_key(key)
        resolved_value = resolve_set_value(resolved, value, stdin=stdin, prompt=prompt, repo=repo)
        _warn_plaintext_token(ctx, resolved)
        removed: list[str] = []
        profile = repo.set_value(
            resolved, resolved_value, dry_run=dry_run, on_spelling_removed=removed.append
        )
        if not dry_run:
            message = f"set {resolved} in profile {profile}{_removed_note(removed)}"
            ui_context(strict=False).success(f"{message} (config: {resolve_config_path()})")
        action = "planned" if dry_run else "updated"
        outcome = SettingOutcome(key=resolved, profile=profile, action=action)
        emit(outcome, fmt=fmt, columns=columns, kind=_SETTING_OUTCOME)


def _warn_plaintext_token(ctx: RootConfigContext, key: str) -> None:
    """Deprecate ``config set <section>.token`` where ``auth set`` can store it instead."""
    section, rest = _split_first(key)
    scope = ctx.sections.get(section)
    if rest == "token" and scope is not None and "token_command" in scope.settings_fields:
        fix = hint(f"auth set {section}")
        ui_context(strict=False).message(
            "warning", f"storing {key} in plain text in config.yml is deprecated\n{fix}"
        )


def _unset(
    ctx: RootConfigContext,
    key: str,
    *,
    dry_run: bool,
    fmt: OutputFormat,
    columns: list[str] | None,
) -> None:
    with report_errors():
        resolved = ctx.resolve_key(key)
        spellings: list[str] = []
        removed, profile = SettingsFileRepository().unset_value(
            resolved, dry_run=dry_run, on_spelling_removed=spellings.append
        )
        ui = ui_context(strict=False)
        where = f"in profile {profile}"
        if not removed:
            ui.message("info", f"{resolved} was not set {where}")
        elif not dry_run:
            ui.success(f"unset {resolved} {where}{_removed_note(spellings)}")
        action = "unchanged" if not removed else "planned" if dry_run else "deleted"
        outcome = SettingOutcome(key=resolved, profile=profile, action=action)
        emit(outcome, fmt=fmt, columns=columns, kind=_SETTING_OUTCOME)


def _removed_note(spellings: list[str]) -> str:
    return f"; removed {', '.join(spellings)}" if spellings else ""


def _migrate(*, dry_run: bool, fmt: OutputFormat, columns: list[str] | None) -> None:
    with report_errors():
        rows = SettingsFileRepository().migrate_keys(dry_run=dry_run)
        ui = ui_context(strict=False)
        if not rows:
            ui.message("info", "no deprecated keys in the config")
        else:
            counts = Counter(row["action"] for row in rows)
            renamed = plural(counts["renamed"], "key")
            dropped = f", dropped {counts['dropped']}" if counts["dropped"] else ""
            if dry_run:
                ui.message("info", f"would rename {renamed}{dropped}")
            else:
                ui.success(f"renamed {renamed}{dropped}")
        if dry_run:
            rows = [{**row, "action": "planned"} for row in rows]
        emit(rows, fmt=fmt, columns=columns, kind=_MIGRATION_OUTCOME)


_SETTING_OUTCOME = "untaped.setting_outcome"
_MIGRATION_OUTCOME = "untaped.config_migration_outcome"


__all__ = [
    "RootConfigContext",
    "RootSectionScope",
    "build_root_config_app",
    "section_scopes",
]
