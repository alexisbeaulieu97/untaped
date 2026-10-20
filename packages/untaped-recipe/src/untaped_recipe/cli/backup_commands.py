"""Backup library commands."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, TypeGuard

from cyclopts import Parameter

from untaped.sdk import (
    ColumnsOption,
    ConfigError,
    Diagnostics,
    DryRunOption,
    Failure,
    FormatOption,
    UntapedError,
    UsageError,
    YesOption,
    batch_apply,
    echo,
    emit,
    finish,
    plural,
    ui_context,
    writes,
)
from untaped_recipe.cli.common import (
    as_recipe_error,
    library_dir,
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
from untaped_recipe.records import Backup, PruneOutcome, RestoreOutcome

_diag = Diagnostics()


def list_command(*, fmt: FormatOption = "table", columns: ColumnsOption = None) -> None:
    """List backup bundles.

    A bundle holds text content only, not mode or mtime, and is owner-only
    (directories `0700`, files `0600`).
    """
    with report_config_errors():
        emit(
            [_backup_row(bundle) for bundle in BackupStore(library_dir() / "backups").list()],
            fmt=fmt,
            columns=columns,
        )


def _backup_row(bundle: BackupBundle, metadata: dict[str, object] | None = None) -> Backup:
    """One bundle; unreadable metadata warns and leaves ``recipe`` empty.

    With its ``metadata`` (``backups get``) the row also carries the
    bundle's ``inputs`` and ``files``.
    """
    if metadata is None:
        try:
            recipe = read_metadata(bundle).get("recipe")
        except ValueError as exc:
            _diag.warning(str(exc))
            recipe = None
        inputs = files = None
    else:
        recipe = metadata.get("recipe")
        inputs = metadata.get("inputs")
        files = metadata.get("files")
    return Backup.model_validate(
        {
            "id": bundle.id,
            "created_at": bundle_created_at(bundle),
            "recipe": recipe if isinstance(recipe, str) else None,
            "path": str(bundle.path),
            "inputs": inputs if isinstance(inputs, dict) else None,
            "files": files if _entries(files) else None,
        }
    )


def _entries(files: object) -> TypeGuard[list[dict[str, object]]]:
    """Whether ``files`` is a list of file entries (a mapping each)."""
    return isinstance(files, list) and all(isinstance(entry, dict) for entry in files)


def get_command(
    backup_id: Annotated[str, Parameter(help="Backup id, an unambiguous prefix, or latest.")],
    /,
    *,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Show backup metadata."""
    with report_config_errors():
        bundle = BackupStore(library_dir() / "backups").resolve(backup_id)
        record = _backup_row(bundle, read_metadata(bundle))
        if fmt != "table":
            emit(record, fmt=fmt, columns=columns)
            return
        # Table view: one line per file instead of a raw list repr; structured
        # formats keep the whole record.
        emit(record.model_dump(mode="json", exclude={"files"}), fmt=fmt, columns=columns)
        if record.files:
            echo("files:")
            for entry in record.files:
                echo(f"  - {entry.get('target', '')}/{entry.get('relative_path', '')}")


@writes
def restore_command(
    backup_id: Annotated[str, Parameter(help="Backup id, an unambiguous prefix, or latest.")],
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
    """Restore a backup bundle.

    The whole bundle is restored as one transaction, after a
    preview and a confirmation. It refuses to overwrite files changed after
    the backup unless --force.

    Examples
    --------
    ```console
    $ untaped recipe backups restore latest --dry-run
    ```
    """
    with report_config_errors():
        store = BackupStore(library_dir() / "backups")
        resolved_id = store.resolve(backup_id).id
        items = store.plan_restore(resolved_id, force=force)
        ui = ui_context(strict=False)
        file_rows = [{"path": str(item.path), "action": item.action} for item in items]

        def _preview(rows: object) -> None:
            del rows
            _diag.info(f"About to restore {plural(len(file_rows), 'file')}:", unmuted=True)
            for row in file_rows:
                _diag.info("  - " + "\t".join(str(value) for value in row.values()), unmuted=True)

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
            row = RestoreOutcome(
                id=resolved_id,
                files=len(items),
                **_outcome_fields(failed.get(resolved_id), dry_run=dry_run, done="restored"),
            )
            emit(row, fmt=fmt, columns=columns)
        if not outcome.any_failed and outcome.results:
            _diag.success(f"restored {resolved_id}")
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
    """Prune old backup bundles.

    Without --keep or --older-than, the `recipe.backup_keep` and
    `recipe.backup_max_age_days` settings decide.
    """
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
        store = BackupStore(library_dir() / "backups")
        bundles = store.list()
        pruned = prune_selection(
            bundles,
            keep=resolved_keep,
            max_age_days=resolved_age,
            now=datetime.now(tz=UTC),
        )
        sizes = {bundle.id: bundle_bytes(bundle) for bundle in pruned}
        ui = ui_context(strict=False)

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
            PruneOutcome(
                id=bundle.id,
                size_bytes=sizes[bundle.id],
                **_outcome_fields(failed.get(bundle.id), dry_run=dry_run, done="deleted"),
            )
            for bundle in pruned
        ]
        emit(rows, fmt=fmt, columns=columns)
        if dry_run:
            _diag.info(
                f"would prune {len(pruned)} of {plural(len(bundles), 'backup')}, "
                f"keep {len(bundles) - len(pruned)}",
            )
            return
        if not outcome.any_failed and (outcome.results or not pruned):
            reclaimed = sum(sizes[bundle.id] for bundle, _ in outcome.results)
            kept = len(bundles) - len(outcome.results)
            _diag.success(
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
    error = Failure.from_exception(failure)
    return {"action": "failed", "detail": error.message, "error": error}
