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
from pydantic import BaseModel

from untaped.core.commands.config_editor import run_config_editor
from untaped.core.commands.config_prompting import resolve_set_value
from untaped.core.commands.notes import print_notes
from untaped.core.commands.records import ConfigMigrationOutcome
from untaped.core.config.models import SettingEntry, SettingRow, setting_entry_row
from untaped.core.config.repository import SettingsFileRepository
from untaped.core.config.use_cases import (
    GetSetting,
    ListAllProfilesSettings,
    ListSettings,
    SetSetting,
    UnsetSetting,
)
from untaped.runtime.config_file import read_config_dict
from untaped.runtime.registry import (
    ApplicationSpec,
    CompositionResult,
    settings_model,
    state_keys,
)
from untaped.runtime.settings import (
    active_settings_layout,
    notify_config_key,
    resolve_config_path,
)
from untaped.runtime.sink import Diagnostics
from untaped.runtime.theme import OutputFormat
from untaped.sdk._cli import (
    ColumnsOption,
    DryRunOption,
    FormatOption,
    create_app,
    echo,
    emit,
    report_errors,
    writes,
)
from untaped.sdk.errors import ConfigError
from untaped.sdk.health.deprecated_keys import (
    NO_KEY_MAPPINGS,
    KeyMappings,
    KeyUse,
    key_mappings,
    notify_use,
)
from untaped.sdk.marks import show_deprecated
from untaped.sdk.network.auth import PLAINTEXT_TOKEN, plaintext_subject, takes_token_command
from untaped.sdk.words import hint, plural

_diag = Diagnostics()


@dataclass(frozen=True)
class RootSectionScope:
    """One section's key-resolution scope for the root config command."""

    plugin: str
    """Owning plugin name (``untaped`` for core's own section)."""

    state_fields: frozenset[str]
    """Tool-managed field names of the section's state model (never settable)."""

    mappings: KeyMappings = NO_KEY_MAPPINGS
    """The section model's renamed, retired and deprecated keys."""

    model: type[BaseModel] | None = None
    """The section's settings model, for the secrets ``auth set`` can store instead."""


@dataclass(frozen=True)
class RootConfigContext:
    """Root key-resolution rule.

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
            target = scope.mappings.readable[rest]
            if not _warned_from_file(first, rest):
                use = KeyUse(rest, target, "renamed", removed_in=scope.mappings.removed_in[rest])
                notify_use(use, section=first, old=key, new=f"{first}.{target}")
            return f"{first}.{target}"
        if rest in scope.mappings.deleted:
            note = scope.mappings.deleted[rest].reason()
            raise ConfigError(
                f"unknown setting: {key!r} ({note})\n{hint('config migrate')}",
                category="invalid",
            )
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


def _warned_from_file(section: str, old: str) -> bool:
    """Warn about ``old`` as reading ``config.yml`` does, migrate hint included, if one has it.

    Reading the file warns about the same key under the same memory, so a
    command that names an old key the file also has warns once.
    """
    try:
        resolved = active_settings_layout().resolve(read_config_dict())
    except ConfigError:
        return False
    for sections in resolved.uses.values():
        for use in sections.get(section, ()):
            if use.old == old:
                notify_config_key(use, section=section)
                return True
    return False


def _split_first(key: str) -> tuple[str, str | None]:
    first, sep, rest = key.partition(".")
    return first, rest if sep else None


def section_scopes(
    shell: ApplicationSpec, result: CompositionResult
) -> dict[str, RootSectionScope]:
    """Build the root resolution scopes for core and composed plugins."""
    scopes: dict[str, RootSectionScope] = {}
    scopes[shell.section] = RootSectionScope(
        plugin=shell.name,
        state_fields=state_keys(shell),
        mappings=key_mappings(shell.settings),
        model=shell.settings,
    )
    for registered in result.plugins:
        spec = registered.spec
        scopes[spec.name] = RootSectionScope(
            plugin=spec.name,
            state_fields=state_keys(spec),
            mappings=key_mappings(settings_model(spec)),
            model=settings_model(spec),
        )
    return scopes


def build_root_config_app(*, shell: ApplicationSpec, result: CompositionResult) -> App:
    """Return the root ``config`` command group for one composition."""
    ctx = RootConfigContext(sections=section_scopes(shell, result))
    app = create_app(name="config", help="Inspect and modify the home's ``config.yml``.")

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
        """Give renamed and retired keys their new names.

        A key also set under its new name (or a closer old name) in the same
        profile is dropped. A deleted key is removed, and the preview prints
        the value it held so you can carry it over. Environment variables
        are not changed.
        """
        _migrate(dry_run=dry_run, fmt=fmt, columns=columns)

    @app.command(name="edit")
    def edit_command() -> None:
        """Open the home's ``config.yml`` in $VISUAL/$EDITOR and re-validate on save."""
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
            for error in list_settings.errors.values():
                # The error already names the section (or env var) and file.
                _diag.warning(f"{error} (its keys show unvalidated values)")
        human = fmt in ("table", "raw")
        if fmt != "table" or columns == ["?"]:
            rows = [setting_entry_row(e, human=human) for e in entries]
            emit(rows, fmt=fmt, columns=columns)
            return
        _emit_split_tables(entries, columns=columns)


_STABLE_COLUMNS = tuple(column for column in SettingRow.table_columns if column != "note")


def _emit_split_tables(entries: list[SettingEntry], *, columns: list[str] | None) -> None:
    """Print the stable settings, then the experimental ones, then the deprecated ones.

    A deprecated setting at its default is left out unless ``--deprecated``;
    set in a file or the environment it is listed, as is every setting read
    through an old spelling (whatever its stability, so the deprecated
    spelling is never missed). Each table has its own columns, so
    ``untaped.ui.hide_empty_columns`` and ``--columns`` apply per table, and only the
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
        _warn_deprecated_setting(ctx, resolved)
        change = SetSetting(repo)(resolved, resolved_value, dry_run=dry_run)
        print_notes(change.notes)
        emit(change.outcome, fmt=fmt, columns=columns)


def _warn_deprecated_setting(ctx: RootConfigContext, key: str) -> None:
    """Warn that ``key`` is a deprecated setting, as reading it from ``config.yml`` does."""
    section, rest = _split_first(key)
    scope = ctx.sections.get(section)
    if rest is not None and scope is not None and rest in scope.mappings.deprecated:
        setting = scope.mappings.deprecated[rest]
        use = KeyUse(
            rest, rest, "deprecated", message=setting.replacement, removed_in=setting.removed_in
        )
        notify_use(use, section=section, old=key, new=key)


def _warn_plaintext_token(ctx: RootConfigContext, key: str) -> None:
    """Deprecate ``config set <section>.token`` of an 11.0 section that ``auth set`` serves."""
    section, rest = _split_first(key)
    scope = ctx.sections.get(section)
    if rest is None or scope is None or scope.model is None:
        return
    # A declared credential's old setting is a deprecated setting, which warns as one.
    if rest != "token" or not takes_token_command(scope.model):
        return
    PLAINTEXT_TOKEN.notify(
        plaintext_subject(section, resolve_config_path().name, field=rest),
        key=section,
        replacement=f"untaped auth set {section}",
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
        change = UnsetSetting(SettingsFileRepository())(ctx.resolve_key(key), dry_run=dry_run)
        print_notes(change.notes)
        emit(change.outcome, fmt=fmt, columns=columns)


def _migrate(*, dry_run: bool, fmt: OutputFormat, columns: list[str] | None) -> None:
    with report_errors():
        keys = SettingsFileRepository().migrate_keys(dry_run=dry_run)
        rows = [{**row, "action": "planned"} for row in keys] if dry_run else keys
        if not rows:
            _diag.info("no renamed, retired or deleted keys in the config")
        else:
            others = _other_readers()
            summary = _migration_summary(keys, past=not dry_run)
            if dry_run:
                _diag.info(f"would {summary}")
            else:
                _diag.success(summary)
            if others:
                _diag.warning(others)
        emit([ConfigMigrationOutcome.model_validate(row) for row in rows], fmt=fmt, columns=columns)


def _other_readers() -> str:
    """Who else may still read the old keys, as one sentence; ``""`` when nobody."""
    from untaped.sdk.storage._refs import others_text  # noqa: PLC0415 - reads refs/ only here

    others = others_text()
    if not others:
        return ""
    return (
        f"other untaped installs here may still read the old keys ({others}): "
        "migrate once every install is upgraded"
    )


def _migration_summary(rows: list[dict[str, str]], *, past: bool) -> str:
    """``rename 2 keys, delete 1``: the counts of each action, nouns first."""
    counts = Counter(row["action"] for row in rows)
    verbs = (("renamed", "rename"), ("dropped", "drop"), ("deleted", "delete"))
    parts = [(done if past else do, counts[done]) for done, do in verbs if counts[done]]
    first, *rest = parts
    return ", ".join(
        [f"{first[0]} {plural(first[1], 'key')}", *(f"{verb} {n}" for verb, n in rest)]
    )


__all__ = [
    "RootConfigContext",
    "RootSectionScope",
    "build_root_config_app",
    "section_scopes",
]
