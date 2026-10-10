"""``untaped setup migrate-dirs``: move or delete the directories older versions left.

Core owns no layout. Each plugin contributes its own rows through
``PluginSpec.migrations`` (:class:`~untaped.plugins.registry.DirMigration`):
a ``preview`` that reads and an ``apply`` that changes. This command previews
every composed plugin's migrations in registry order (by plugin name), shows
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

from untaped.cli import echo, emit, report_declined
from untaped.config_file import read_config_dict
from untaped.errors import ConfigError, OperationCancelledError, UntapedError
from untaped.messages import plural, shown_path
from untaped.migrations import overlapping
from untaped.plugins.registry import (
    CompositionResult,
    DirMigration,
    MigrationOptions,
    MigrationOutcome,
    MigrationRow,
    PluginContext,
    PluginSpec,
    section_settings,
)
from untaped.settings import active_settings_layout, check_settings_field
from untaped.theme import OutputFormat
from untaped.ui import ui_context

#: The command a doctor row or hint names.
MIGRATE_COMMAND = "setup migrate-dirs"
#: Preview actions that change something on disk.
_CHANGES = frozenset({"move", "delete"})
_ROW_KIND = "untaped.migration_row"


@dataclass(frozen=True)
class Planned:
    """One composed migration and what its preview returned (or why it failed)."""

    plugin: str
    migration: DirMigration
    rows: tuple[MigrationRow, ...] = ()
    error: str | None = None

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
    for registered in result.plugins:
        spec = registered.spec
        for migration in spec.migrations:
            try:
                rows = tuple(_rows(migration.preview(contexts[spec.name], options)))
            except Exception as exc:  # a plugin's bug is its row's failure, not the command's
                planned.append(Planned(spec.name, migration, error=_raised(exc)))
                continue
            planned.append(Planned(spec.name, migration, rows))
    return _clashes(planned)


def _clashes(planned: list[Planned]) -> list[Planned]:
    """Fail both migrations when one would delete a directory the other moves or keeps."""
    errors: dict[int, str] = {}
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
                    index, f"would delete {shown}, which {other.migration.id} uses; nothing ran"
                )
                errors.setdefault(
                    other_index,
                    f"{item.migration.id} would delete {shown}, which this uses; nothing ran",
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


def preview_records(planned: Sequence[Planned]) -> list[dict[str, object]]:
    """The preview as rows: the migration's id beside each of its rows."""
    records: list[dict[str, object]] = []
    for item in planned:
        if item.error is not None:
            records.append({"id": item.migration.id, "action": "failed", "detail": item.error})
            continue
        records.extend({"id": item.migration.id, **row.model_dump()} for row in item.rows)
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
            ui.message("info", "nothing to migrate")
        _emit_preview(records, fmt=fmt, columns=columns)
        return any(item.error is not None for item in planned)
    if not any(item.changes or item.error for item in planned):
        _emit_preview(records, fmt=fmt, columns=columns)
        ui.message("info", "nothing to move or delete")
        return False
    if not yes:
        with ui.terminal(refusal="setup migrate-dirs requires --yes when not interactive"):
            echo("About to migrate:", err=True)
            for line in _preview_lines(records):
                echo(f"  {line}", err=True)
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
            detail = f"not applied: {item.error}"
            outcomes.append(MigrationOutcome(id=migration.id, action="failed", detail=detail))
            continue
        try:
            done = migration.apply(contexts[item.plugin], options)
            outcomes.extend(_outcomes(migration, done))
        except Exception as exc:  # one migration's failure never stops the others
            outcomes.append(MigrationOutcome(id=migration.id, action="failed", detail=_raised(exc)))
    return outcomes


def _outcomes(migration: DirMigration, done: object) -> list[MigrationOutcome]:
    if not isinstance(done, Sequence) or not all(
        isinstance(outcome, MigrationOutcome) for outcome in done
    ):
        detail = f"apply returned {type(done).__name__}, expected MigrationOutcome rows"
        return [MigrationOutcome(id=migration.id, action="failed", detail=detail)]
    return list(done)


def _rows(rows: object) -> Sequence[MigrationRow]:
    if not isinstance(rows, Sequence) or not all(isinstance(row, MigrationRow) for row in rows):
        raise TypeError(f"preview returned {type(rows).__name__}, expected MigrationRow rows")
    return rows


def _raised(exc: Exception) -> str:
    if isinstance(exc, UntapedError):
        return str(exc)
    return f"raised {type(exc).__name__}: {exc}"


def _declined() -> bool:
    report_declined(OperationCancelledError())
    return True


def _shown(records: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    """Records with home-relative paths written ``~/…``, for people (JSON keeps them whole)."""
    return [
        {
            key: shown_path(str(value)) if key in ("source", "destination") and value else value
            for key, value in record.items()
        }
        for record in records
    ]


def _emit_preview(
    records: list[dict[str, object]], *, fmt: OutputFormat, columns: list[str] | None
) -> None:
    emit(
        _shown(records) if fmt == "table" else records,
        fmt=fmt,
        columns=columns,
        kind=_ROW_KIND,
        table_columns=("id", "action", "source", "destination", "detail"),
        empty=False,
    )


def _preview_lines(records: Sequence[Mapping[str, object]]) -> list[str]:
    lines = []
    for record in _shown(records):
        source, destination = record.get("source") or "", record.get("destination") or ""
        where = f"{source} → {destination}" if destination else str(source)
        parts = [str(record["action"]), where, str(record.get("detail") or "")]
        lines.append("  ".join(part for part in parts if part))
    return lines


def _contexts(result: CompositionResult) -> dict[str, PluginContext]:
    """Each plugin's context: its validated settings, ``None`` when they don't validate."""
    try:
        effective: Mapping[str, Any] | None = active_settings_layout().effective(read_config_dict())
    except ConfigError:
        effective = None
    return {
        registered.spec.name: PluginContext(settings=_settings(registered.spec, effective))
        for registered in result.plugins
    }


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


__all__ = ["MIGRATE_COMMAND", "Planned", "migrate_dirs", "plan", "summary_text"]
