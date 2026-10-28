"""``untaped config …``: list, get, set, unset, migrate and edit settings.

Key resolution is direct: a fully qualified ``section.key``
selects its schema by ``section``. A renamed key resolves to its new name
with a warning and a retired one is rejected; the section's own state fields
raise the "managed by" error, and anything else passes through to the
schema. Bare keys are never implicitly expanded to a plugin section.

The commands call the config use cases; :class:`RootConfigContext` is the
key-resolution rule, built from the run's composition. There is
deliberately no ``config doctor``: config diagnostics live at the root
``doctor`` command, where one broken section cannot block the other rows.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass

from pydantic import Field

from untaped.core.commands.config_editor import run_config_editor
from untaped.core.commands.config_prompting import resolve_set_value
from untaped.core.commands.notes import print_notes
from untaped.core.commands.records import ConfigMigrationOutcome
from untaped.core.config.models import Setting, SettingEntry, SettingOutcome, setting_entry_row
from untaped.core.config.repository import SettingsFileRepository
from untaped.core.config.use_cases import (
    GetSetting,
    ListAllProfilesSettings,
    ListSettings,
    SetSetting,
    UnsetSetting,
)
from untaped.core.settings import CoreSettings
from untaped.runtime.config_file import read_config_dict
from untaped.runtime.registry import (
    CompositionResult,
    settings_model,
    state_keys,
)
from untaped.runtime.settings import (
    active_settings_layout,
    notify_config_key,
)
from untaped.sdk._cli import echo, emit
from untaped.sdk.errors import ConfigError
from untaped.sdk.fields import DryRun, Output, Positional, default_columns
from untaped.sdk.health.deprecated_keys import (
    NO_KEY_MAPPINGS,
    KeyMappings,
    KeyUse,
    key_mappings,
    notify_use,
)
from untaped.sdk.marks import show_deprecated
from untaped.sdk.plugin import Command, Group
from untaped.sdk.words import hint, plural


@dataclass(frozen=True)
class RootSectionScope:
    """One section's key-resolution scope for the root config command."""

    plugin: str
    """Owning plugin name (``untaped`` for core's own section)."""

    state_fields: frozenset[str]
    """Tool-managed field names of the section's state model (never settable)."""

    mappings: KeyMappings = NO_KEY_MAPPINGS
    """The section model's renamed, retired and deprecated keys."""


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


def section_scopes(result: CompositionResult) -> dict[str, RootSectionScope]:
    """Build the root resolution scopes of every composed plugin, core's included."""
    return {
        spec.name: RootSectionScope(
            plugin=spec.name,
            state_fields=state_keys(spec),
            mappings=key_mappings(settings_model(spec)),
        )
        for spec in (registered.spec for registered in result.plugins)
    }


def _context() -> RootConfigContext:
    """The key-resolution rule for this run's composition: core's section and each plugin's."""
    # Lazy: bootstrap is the CLI's root and imports this module.
    from untaped.runtime.bootstrap import composition  # noqa: PLC0415

    return RootConfigContext(sections=section_scopes(composition()))


class List(Command[CoreSettings]):
    """List the settings of every installed plugin and untaped's own, as this run reads them.

    A table lists stable settings, then experimental ones, then deprecated
    ones set somewhere (every one with --deprecated). Secrets show as ***
    unless --show-secrets.

    Examples:
        $ untaped config list
        $ untaped config list --all-profiles -f json
    """

    show_secrets: bool = Field(False, description="Reveal secret values instead of ***.")
    all_profiles: bool = Field(
        False, description="One row per profile and key instead of the resolved view."
    )
    output: Output[Setting]

    def run(self) -> Iterator[Setting]:
        repo = SettingsFileRepository()
        if self.all_profiles:
            entries = ListAllProfilesSettings(repo)(reveal_secrets=self.show_secrets)
        else:
            list_settings = ListSettings(repo)
            entries = list_settings(reveal_secrets=self.show_secrets)
            for error in list_settings.errors.values():
                # The error already names the section (or env var) and file.
                self.ctx.diag.warning(f"{error} (its keys show unvalidated values)")
        fmt = self.output.format
        if fmt == "table" and self.output.requested != ["?"]:
            _emit_split_tables(entries, columns=self.output.requested)
            return
        human = fmt in ("table", "raw")
        yield from (setting_entry_row(entry, human=human) for entry in entries)


class Get(Command[CoreSettings]):
    """Print one setting's value as this run reads it.

    Raw output is the value alone (nothing when unset; a mapping or list as
    JSON); other formats give its default and where it came from.

    Examples:
        $ untaped config get awx.server.url
        $ untaped config get awx.server.url -f json
    """

    key: Positional[str] = Field(description="The setting's full key (section.key).")
    show_secrets: bool = Field(False, description="Reveal a secret value instead of ***.")
    output: Output[Setting] = Output(
        format="raw",
        columns=("key", "value", "default", "source"),
        render={"raw": lambda row: _print_value(row)},
    )

    def run(self) -> Setting:
        entry = GetSetting(SettingsFileRepository())(
            _context().resolve_key(self.key), reveal_secrets=self.show_secrets
        )
        return setting_entry_row(entry, human=self.output.format in ("table", "raw"))


def _print_value(row: Setting) -> None:
    if row.value is not None:
        echo(str(row.value))


class Set(Command[CoreSettings]):
    """Set one setting in the profile --profile names, else the base.

    The value is checked against the setting's type before anything is
    written; --prompt asks for it by type, --stdin reads it.

    Examples:
        $ untaped config set awx.server.url https://aap.example.com
        $ printf %s "$URL" | untaped config set awx.server.url --stdin
    """

    key: Positional[str] = Field(description="The setting's full key (section.key).")
    value: Positional[str | None] = Field(None, description="The new value, checked for its type.")
    stdin: bool = Field(False, description="Read the value from stdin.")
    prompt: bool = Field(False, description="Ask for the value, by the setting's type.")
    dry_run: DryRun
    output: Output[SettingOutcome]

    def run(self) -> SettingOutcome:
        ctx = _context()
        repo = SettingsFileRepository()
        resolved = ctx.resolve_key(self.key)
        value = resolve_set_value(
            resolved, self.value, stdin=self.stdin, prompt=self.prompt, repo=repo
        )
        _warn_deprecated_setting(ctx, resolved)
        change = SetSetting(repo)(resolved, value, dry_run=bool(self.dry_run))
        print_notes(change.notes)
        return change.outcome


class Unset(Command[CoreSettings]):
    """Remove one setting from the profile --profile names, else the base.

    Examples:
        $ untaped config unset awx.server.url
    """

    key: Positional[str] = Field(description="The setting's full key (section.key).")
    dry_run: DryRun
    output: Output[SettingOutcome]

    def run(self) -> SettingOutcome:
        resolved = _context().resolve_key(self.key)
        change = UnsetSetting(SettingsFileRepository())(resolved, dry_run=bool(self.dry_run))
        print_notes(change.notes)
        return change.outcome


class Migrate(Command[CoreSettings]):
    """Give renamed and retired keys their new names.

    A key also set under its new name (or a closer old name) in the same
    profile is dropped. A deleted key is removed, and the preview prints
    the value it held so you can carry it over. Environment variables are
    not changed.

    Examples:
        $ untaped config migrate --dry-run
        $ untaped config migrate
    """

    dry_run: DryRun
    output: Output[ConfigMigrationOutcome]

    def run(self) -> Iterator[ConfigMigrationOutcome]:
        keys = SettingsFileRepository().migrate_keys(dry_run=bool(self.dry_run))
        rows = [{**row, "action": "planned"} for row in keys] if self.dry_run else keys
        diag = self.ctx.diag
        if not rows:
            diag.info("no renamed, retired or deleted keys in the config")
        else:
            others = _other_readers()
            summary = _migration_summary(keys, past=not self.dry_run)
            if self.dry_run:
                diag.info(f"would {summary}")
            else:
                diag.success(summary)
            if others:
                diag.warning(others)
        yield from (ConfigMigrationOutcome.model_validate(row) for row in rows)


class Edit(Command[CoreSettings]):
    """Open the home's config.yml in $VISUAL or $EDITOR; it is checked before it is saved.

    Examples:
        $ untaped config edit
    """

    def run(self) -> None:
        run_config_editor()


class Config(Group):
    """Inspect and change the settings in the home's config.yml."""

    commands = (List, Get, Set, Unset, Migrate, Edit)


_STABLE_COLUMNS = tuple(column for column in default_columns(Setting) if column != "note")


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
        ("Deprecated", deprecated, default_columns(Setting)),
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


__all__ = ["Config", "RootConfigContext", "RootSectionScope", "section_scopes"]
