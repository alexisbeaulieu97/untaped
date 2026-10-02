"""Backup library commands."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from cyclopts import Parameter
from pydantic import BaseModel, ConfigDict

from untaped.sdk import (
    ColumnsOption,
    ConfigError,
    DryRunOption,
    ErrorInfo,
    FormatOption,
    OutcomeRecord,
    UntapedError,
    UsageError,
    UtcTimestamp,
    YesOption,
    batch_apply,
    echo,
    emit,
    finish,
    plural,
    render_rows,
    writes,
)
from untaped_recipe.cli._context import recipe_ui
from untaped_recipe.cli.common import (
    as_recipe_error,
    library_root,
    report_config_errors,
    settings,
)
from untaped_recipe.infrastructure.backup import (
    BackupBundle,
    BackupStore,
    bundle_bytes,
    bundle_created_at,
    prune_selection,
    read_metadata,
)


class BackupPruneRecord(OutcomeRecord):
    """One ``backups prune`` row (kind ``recipe.prune_outcome``).

    ``action`` is ``planned`` (``--dry-run``), ``deleted`` or ``failed`` (with
    ``detail`` and ``error``).
    """

    id: str
    size_bytes: int
    detail: str | None = None


class BackupRestoreRecord(OutcomeRecord):
    """The ``backups restore`` row (kind ``recipe.restore_outcome``).

    ``files`` counts the bundle's files. ``action`` is ``planned``
    (``--dry-run``), ``restored`` or ``failed`` (with ``detail`` and ``error``).
    """

    id: str
    files: int
    detail: str | None = None


def list_command(*, fmt: FormatOption = "table", columns: ColumnsOption = None) -> None:
    """List backup bundles."""
    with report_config_errors():
        emit(
            [_backup_row(bundle) for bundle in BackupStore(library_root() / "backups").list()],
            fmt=fmt,
            columns=columns,
            kind="recipe.backup",
            table_columns=["id", "created_at", "recipe"],
        )


class BackupListRecord(BaseModel):
    """One ``backups list`` row (kind ``recipe.backup``).

    ``created_at`` comes from the bundle id; ``recipe`` from its metadata
    (``None`` when that cannot be read).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    created_at: UtcTimestamp | None
    recipe: str | None
    path: str


def _backup_row(bundle: BackupBundle) -> BackupListRecord:
    """One ``backups list`` row; unreadable metadata warns and leaves ``recipe`` empty."""
    try:
        recipe = read_metadata(bundle).get("recipe")
    except ValueError as exc:
        recipe_ui().message("warning", str(exc))
        recipe = None
    return BackupListRecord(
        id=bundle.id,
        created_at=bundle_created_at(bundle),
        recipe=recipe if isinstance(recipe, str) else None,
        path=str(bundle.path),
    )


def get_command(
    backup_id: Annotated[str, Parameter(help="Backup id, prefix, or latest.")],
    /,
    *,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Show backup metadata."""
    with report_config_errors():
        metadata = BackupStore(library_root() / "backups").metadata(backup_id)
        if fmt != "table":
            emit(metadata, fmt=fmt, columns=columns, kind="recipe.backup")
            return
        # Table view: one line per file instead of a raw list repr; structured
        # formats keep the full metadata mapping.
        files = metadata.get("files")
        emit(
            {key: value for key, value in metadata.items() if key != "files"},
            fmt=fmt,
            columns=columns,
            kind="recipe.backup",
        )
        if isinstance(files, list) and files:
            echo("files:")
            for entry in files:
                if isinstance(entry, dict):
                    echo(f"  - {entry.get('target', '')}/{entry.get('relative_path', '')}")
                else:
                    echo(f"  - {entry}")


@writes
def restore_command(
    backup_id: Annotated[str, Parameter(help="Backup id, prefix, or latest.")],
    /,
    *,
    force: Annotated[
        bool,
        Parameter(name="--force", negative="", help="Overwrite files changed after backup."),
    ] = False,
    yes: YesOption = False,
    dry_run: DryRunOption = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Restore a backup bundle."""
    with report_config_errors():
        store = BackupStore(library_root() / "backups")
        resolved_id = store.resolve(backup_id).id
        items = store.plan_restore(resolved_id, force=force)
        ui = recipe_ui()
        file_rows = [{"path": str(item.path), "action": item.action} for item in items]

        def _preview(rows: object) -> None:
            del rows
            echo(f"About to restore {plural(len(file_rows), 'file')}:", err=True)
            for row in file_rows:
                echo("  - " + "\t".join(str(value) for value in row.values()), err=True)

        def _restore(bundle_id: str) -> str:
            # One store.restore call: the bundle resolves once and all files
            # flush in a single staged transaction (full set or rolled back).
            store.restore(bundle_id, force=force)
            return bundle_id

        outcome = batch_apply(
            [resolved_id],
            _restore,
            verb="restore",
            noun="backup",
            label=lambda bundle_id: bundle_id,
            describe=lambda bundle_id: {"id": bundle_id, "files": len(items)},
            ui=ui,
            destructive=True,
            assume_yes=yes,
            preview_only=dry_run,
            preview=_preview,
        )
        if dry_run:
            _preview(outcome.planned_rows)
        if outcome.cancelled:
            finish(outcome)
        if fmt != "table":
            failed = {bundle_id: exc for bundle_id, exc in outcome.failures}
            row = BackupRestoreRecord(
                id=resolved_id,
                files=len(items),
                **_outcome_fields(failed.get(resolved_id), dry_run=dry_run, done="restored"),
            )
            emit(row, fmt=fmt, columns=columns, kind="recipe.restore_outcome")
        if not outcome.any_failed and outcome.results:
            ui.message("success", f"restored {resolved_id}")
        finish(outcome)


@writes(destructive=True)
def prune_command(
    *,
    keep: Annotated[
        int | None,
        Parameter(name="--keep", help="Keep only the newest N bundles."),
    ] = None,
    older_than: Annotated[
        int | None,
        Parameter(name="--older-than", help="Prune bundles older than DAYS days."),
    ] = None,
    yes: YesOption = False,
    dry_run: DryRunOption = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Prune old backup bundles."""
    with report_config_errors():
        if keep is not None and keep < 1:
            raise UsageError("--keep must be at least 1")
        if older_than is not None and older_than < 1:
            raise UsageError("--older-than must be at least 1")
        resolved_keep = settings().backup_keep if keep is None else keep
        resolved_age = settings().backup_max_age_days if older_than is None else older_than
        if resolved_keep is None and resolved_age is None:
            raise ConfigError(
                "backups prune needs --keep/--older-than "
                "or backup_keep/backup_max_age_days settings"
            )
        store = BackupStore(library_root() / "backups")
        bundles = store.list()
        pruned = prune_selection(
            bundles,
            keep=resolved_keep,
            max_age_days=resolved_age,
            now=datetime.now(tz=UTC),
        )
        sizes = {bundle.id: bundle_bytes(bundle) for bundle in pruned}
        ui = recipe_ui()

        @as_recipe_error
        def _delete(bundle: BackupBundle) -> BackupBundle:
            store.delete(bundle.id)
            return bundle

        outcome = batch_apply(
            pruned,
            _delete,
            verb="prune",
            noun="backup",
            label=lambda bundle: bundle.id,
            describe=lambda bundle: {"id": bundle.id, "size_bytes": sizes[bundle.id]},
            ui=ui,
            destructive=True,
            assume_yes=yes,
            preview_only=dry_run,
        )
        if outcome.cancelled:
            finish(outcome)
        failed = {bundle.id: exc for bundle, exc in outcome.failures}
        rows = [
            BackupPruneRecord(
                id=bundle.id,
                size_bytes=sizes[bundle.id],
                **_outcome_fields(failed.get(bundle.id), dry_run=dry_run, done="deleted"),
            ).model_dump()
            for bundle in pruned
        ]
        rendered = render_rows(rows, fmt=fmt, columns=columns, kind="recipe.prune_outcome")
        if rendered:
            echo(rendered)
        if dry_run:
            ui.message(
                "info",
                f"would prune {len(pruned)} of {plural(len(bundles), 'backup')}, "
                f"keep {len(bundles) - len(pruned)}",
            )
            return
        if not outcome.any_failed and (outcome.results or not pruned):
            reclaimed = sum(sizes[bundle.id] for bundle, _ in outcome.results)
            kept = len(bundles) - len(outcome.results)
            ui.message(
                "success",
                f"pruned {len(outcome.results)} of {plural(len(bundles), 'backup')}, "
                f"kept {kept}, reclaimed {reclaimed} bytes",
            )
        finish(outcome)


def _outcome_fields(failure: UntapedError | None, *, dry_run: bool, done: str) -> dict[str, Any]:
    """A backup row's ``action``, ``detail`` and ``error``: planned, ``done`` or failed."""
    if dry_run:
        return {"action": "planned"}
    if failure is None:
        return {"action": done}
    error = ErrorInfo.from_exception(failure)
    return {"action": "failed", "detail": error.message, "error": error}
