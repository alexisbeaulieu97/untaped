"""``untaped setup migrate-dirs``: move or delete the directories older versions left.

Core owns no layout; its rows offer the reference files of installs that
are gone (:func:`~untaped.sdk.health.migrations.references_migration`) and the caches
of plugins not installed here
(:func:`~untaped.sdk.health.migrations.uninstalled_caches_migration`). Each plugin
contributes its own rows through ``PluginSpec.migrations``
(:class:`~untaped.runtime.registry.DirMigration`): a ``preview`` that reads
and an ``apply`` that changes. This command previews core's rows, then every
composed plugin's migrations in registry order (by plugin name), shows
them as one table, confirms (``--yes`` skips it; with no terminal it exits 2),
then applies them in the same order and reports one ``untaped.migration``
outcome per migration. A migration that fails is a ``failed`` row naming its
id; the others still run, and the command exits 1. A migration whose preview
failed is never applied, and neither are two migrations when one would
delete what the other moves or keeps (two old settings naming one
directory): both are ``failed``, naming each other. ``--dry-run`` stops
after the preview. Doctor's ``migrate-dirs`` row runs the same previews.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from untaped.core.commands.records import MigrationPreviewRow
from untaped.core.plugin import NAME
from untaped.runtime.config_file import read_config_dict
from untaped.runtime.registry import (
    CompositionResult,
    DirMigration,
    MigrationOptions,
    MigrationOutcome,
    MigrationRow,
    PluginContext,
    PluginSpec,
    section_settings,
)
from untaped.runtime.settings import active_settings_layout, check_settings_field
from untaped.runtime.sink import Diagnostics
from untaped.runtime.theme import OutputFormat
from untaped.sdk._cli import emit, report_declined
from untaped.sdk._ui import ui_context
from untaped.sdk.diagnostic import Failure
from untaped.sdk.errors import ConfigError, OperationCancelledError, UntapedError
from untaped.sdk.health.migrations import (
    overlapping,
    references_migration,
    uninstalled_caches,
    uninstalled_caches_migration,
)
from untaped.sdk.words import plural, shown_path

_diag = Diagnostics()

#: The command a doctor row or hint names.
MIGRATE_COMMAND = "setup migrate-dirs"
#: Preview actions that change something on disk.
_CHANGES = frozenset({"move", "delete"})
#: The context key of core's own migrations (the references of installs that are gone).
_CORE = "untaped"


@dataclass(frozen=True)
class Planned:
    """One composed migration and what its preview returned (or why it failed)."""

    plugin: str
    migration: DirMigration
    rows: tuple[MigrationRow, ...] = ()
    error: Failure | None = None

    @property
    def changes(self) -> bool:
        return any(row.action in _CHANGES for row in self.rows)


def migrate_dirs(
    result: CompositionResult,
    *,
    dissociate: bool,
    yes: bool,
    dry_run: bool,
    fmt: OutputFormat,
    columns: list[str] | None,
) -> bool:
    """Run ``setup migrate-dirs``; whether anything failed (or the user declined)."""
    options = MigrationOptions(dry_run=dry_run, dissociate=dissociate)
    return _run(result, options, yes=yes, fmt=fmt, columns=columns)


def plan(result: CompositionResult, options: MigrationOptions) -> list[Planned]:
    """Every composed migration's preview, in registry order; a preview that raises is kept."""
    contexts = _contexts(result)
    planned: list[Planned] = []
    for plugin, migration in _migrations(result):
        try:
            rows = tuple(_rows(migration.preview(contexts[plugin], options)))
        except Exception as exc:  # a plugin's bug is its row's failure, not the command's
            planned.append(Planned(plugin, migration, error=_raised(exc)))
            continue
        planned.append(Planned(plugin, migration, rows))
    return _clashes(planned)


def _migrations(result: CompositionResult) -> list[tuple[str, DirMigration]]:
    """Core's own migrations first (each only while it has something), then each plugin's."""
    from untaped.sdk.storage._refs import read_references  # noqa: PLC0415 - reads refs/ only here

    dead = any(ref.opaque is None and not ref.live for ref in read_references())
    found = [(_CORE, references_migration())] if dead else []
    installed = installed_names(result)
    if uninstalled_caches(installed):
        found.append((_CORE, uninstalled_caches_migration(installed)))
    for registered in result.plugins:
        found += [(registered.spec.name, migration) for migration in registered.spec.migrations]
    return found


def installed_names(result: CompositionResult) -> frozenset[str]:
    """Every plugin installed here: composed or quarantined (its data is its own), and core."""
    return frozenset(
        [NAME]
        + [registered.spec.name for registered in result.plugins]
        + [record.name for record in result.quarantine]
    )


def _clashes(planned: list[Planned]) -> list[Planned]:
    """Fail both migrations when one would delete a directory the other moves or keeps."""
    errors: dict[int, Failure] = {}
    for index, item in enumerate(planned):
        for row in item.rows:
            if row.action != "delete" or not row.source:
                continue
            for other_index, other in enumerate(planned):
                if other_index == index:
                    continue
                used = _paths(other.rows)
                hit = next((path for path in used if overlapping(Path(row.source), path)), None)
                if hit is None:
                    continue
                shown = shown_path(row.source)
                errors.setdefault(
                    index,
                    Failure.plain(
                        f"would delete {shown}, which {other.migration.id} uses; nothing ran"
                    ),
                )
                errors.setdefault(
                    other_index,
                    Failure.plain(
                        f"{item.migration.id} would delete {shown}, which this uses; nothing ran"
                    ),
                )
    return [
        replace(item, error=errors[index]) if index in errors and item.error is None else item
        for index, item in enumerate(planned)
    ]


def _paths(rows: Sequence[MigrationRow]) -> list[Path]:
    paths = []
    for row in rows:
        for value in (row.source, row.destination):
            if value and Path(value).is_absolute():
                paths.append(Path(value))
    return paths


def preview_records(planned: Sequence[Planned]) -> list[MigrationPreviewRow]:
    """The preview as rows: the migration's id beside each of its rows."""
    records: list[MigrationPreviewRow] = []
    for item in planned:
        migration_id = item.migration.id
        if item.error is not None:
            records.append(
                MigrationPreviewRow(
                    id=migration_id, action="failed", detail=item.error.message, error=item.error
                )
            )
            continue
        # A plugin's row may subclass MigrationRow; the preview carries the base fields.
        records.extend(
            MigrationPreviewRow(
                id=migration_id, **{name: getattr(row, name) for name in MigrationRow.model_fields}
            )
            for row in item.rows
        )
    return records


def _run(
    result: CompositionResult,
    options: MigrationOptions,
    *,
    yes: bool,
    fmt: OutputFormat,
    columns: list[str] | None,
) -> bool:
    ui = ui_context(strict=False)
    planned = plan(result, options)
    records = preview_records(planned)
    if options.dry_run or not records:
        if not records:
            _diag.info("nothing to migrate")
        _emit_preview(records, fmt=fmt, columns=columns)
        return any(item.error is not None for item in planned)
    if not any(item.changes or item.error for item in planned):
        _emit_preview(records, fmt=fmt, columns=columns)
        _diag.info("nothing to move or delete")
        return False
    if not yes:
        with ui.terminal(refusal="setup migrate-dirs requires --yes when not interactive"):
            _diag.info("About to migrate:", unmuted=True)
            for line in _preview_lines(records):
                _diag.info(f"  {line}", unmuted=True)
            if not ui.confirm("Continue?"):
                return _declined()
    outcomes = _apply(result, planned, options)
    emit(outcomes, fmt=fmt, columns=columns)
    return any(outcome.failed for outcome in outcomes)


def _apply(
    result: CompositionResult, planned: Sequence[Planned], options: MigrationOptions
) -> list[MigrationOutcome]:
    contexts = _contexts(result)
    outcomes: list[MigrationOutcome] = []
    for item in planned:
        migration = item.migration
        if item.error is not None:
            detail = f"not applied: {item.error.message}"
            outcomes.append(_failed(migration, item.error, detail=detail))
            continue
        try:
            done = migration.apply(contexts[item.plugin], options)
            outcomes.extend(_outcomes(migration, done))
        except Exception as exc:  # one migration's failure never stops the others
            outcomes.append(_failed(migration, _raised(exc)))
    return outcomes


def _outcomes(migration: DirMigration, done: object) -> list[MigrationOutcome]:
    if not isinstance(done, Sequence) or not all(
        isinstance(outcome, MigrationOutcome) for outcome in done
    ):
        detail = f"apply returned {type(done).__name__}, expected MigrationOutcome rows"
        return [_failed(migration, Failure.plain(detail))]
    # A failed or partial row a migration built itself still carries a structured error.
    return [
        outcome.model_copy(update={"error": Failure.plain(outcome.detail)})
        if outcome.failed and outcome.error is None
        else outcome
        for outcome in done
    ]


def _failed(
    migration: DirMigration, error: Failure, *, detail: str | None = None
) -> MigrationOutcome:
    """A ``failed`` outcome carrying ``error``; ``detail`` defaults to its message."""
    text = error.message if detail is None else detail
    return MigrationOutcome(id=migration.id, action="failed", detail=text, error=error)


def _rows(rows: object) -> Sequence[MigrationRow]:
    if not isinstance(rows, Sequence) or not all(isinstance(row, MigrationRow) for row in rows):
        raise TypeError(f"preview returned {type(rows).__name__}, expected MigrationRow rows")
    return rows


def _raised(exc: Exception) -> Failure:
    """``exc`` as a row's failure: an untaped error as itself, a plugin's bug as what it raised."""
    if isinstance(exc, UntapedError):
        return Failure.from_exception(exc)
    return Failure.from_exception(exc, message=f"raised {type(exc).__name__}: {exc}")


def _declined() -> bool:
    report_declined(OperationCancelledError())
    return True


def _shown(records: Sequence[MigrationPreviewRow]) -> list[MigrationPreviewRow]:
    """Records with home-relative paths written ``~/…``, for people (JSON keeps them whole)."""
    return [
        record.model_copy(
            update={
                key: shown_path(value)
                for key in ("source", "destination")
                if (value := getattr(record, key))
            }
        )
        for record in records
    ]


def _emit_preview(
    records: list[MigrationPreviewRow], *, fmt: OutputFormat, columns: list[str] | None
) -> None:
    emit(_shown(records) if fmt == "table" else records, fmt=fmt, columns=columns, empty=False)


def _preview_lines(records: Sequence[MigrationPreviewRow]) -> list[str]:
    lines = []
    for record in _shown(records):
        source, destination = record.source or "", record.destination or ""
        where = f"{source} → {destination}" if destination else source
        parts = [record.action, where, record.detail]
        lines.append("  ".join(part for part in parts if part))
    return lines


def _contexts(result: CompositionResult) -> dict[str, PluginContext]:
    """Each plugin's context: its validated settings, ``None`` when they don't validate."""
    try:
        effective: Mapping[str, Any] | None = active_settings_layout().effective(read_config_dict())
    except ConfigError:
        effective = None
    contexts = {
        registered.spec.name: PluginContext(settings=_settings(registered.spec, effective))
        for registered in result.plugins
    }
    return {_CORE: PluginContext(settings=None), **contexts}


def _settings(spec: PluginSpec, effective: Mapping[str, Any] | None) -> Any:
    model = section_settings(spec)
    if model is None or effective is None:
        return None
    node = effective.get(spec.name, {})
    if not isinstance(node, dict):
        return None
    try:
        return check_settings_field(spec.name, node, model=model)
    except ConfigError:
        return None


def summary_text(planned: Sequence[Planned]) -> str:
    """``3 directories to migrate``: what doctor's row says (sizes are migrate-dirs' to measure)."""
    count = sum(1 for item in planned for row in item.rows if row.action in _CHANGES)
    return f"{plural(count, 'directory', 'directories')} to migrate"


__all__ = ["MIGRATE_COMMAND", "Planned", "installed_names", "migrate_dirs", "plan", "summary_text"]
