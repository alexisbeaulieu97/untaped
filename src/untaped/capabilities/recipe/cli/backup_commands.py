"""Backup library commands."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from cyclopts import Parameter

from untaped.capabilities.recipe.cli._context import recipe_ui
from untaped.capabilities.recipe.cli.common import library_root, report_config_errors, settings
from untaped.capabilities.recipe.infrastructure.backup import (
    BackupBundle,
    BackupStore,
    bundle_bytes,
    prune_selection,
)
from untaped.capability_api import (
    ColumnsOption,
    ConfigError,
    DryRunOption,
    FormatOption,
    UsageError,
    YesOption,
    batch_apply,
    create_app,
    echo,
    emit,
    finish,
    plural,
    render_rows,
)

app = create_app(name="backup", help="Manage recipe backups.")


@app.command(name="list")
def list_command(*, fmt: FormatOption = "table", columns: ColumnsOption = None) -> None:
    """List backup bundles."""
    with report_config_errors():
        rows: list[dict[str, object]] = [
            {"id": bundle.id, "path": str(bundle.path)}
            for bundle in BackupStore(library_root() / "backups").list()
        ]
        rendered = render_rows(rows, fmt=fmt, columns=columns, kind="recipe.backup")
        if rendered:
            echo(rendered)


@app.command(name="get")
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


@app.command(name="restore")
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
) -> None:
    """Restore a backup bundle."""
    with report_config_errors():
        store = BackupStore(library_root() / "backups")
        items = store.plan_restore(backup_id, force=force)
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
            [backup_id],
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
        if not outcome.any_failed and outcome.results:
            ui.message("success", f"restored {backup_id}")
        finish(outcome)


@app.command(name="prune")
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
                "backup prune needs --keep/--older-than or backup_keep/backup_max_age_days settings"
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

        def _delete(bundle: BackupBundle) -> BackupBundle:
            try:
                store.delete(bundle.id)
            except (ValueError, OSError) as exc:
                # batch_apply only counts UntapedError as a per-item failure;
                # anything else would abort the whole batch mid-prune.
                raise ConfigError(str(exc)) from exc
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
        if dry_run:
            rendered = render_rows(
                outcome.planned_rows, fmt=fmt, columns=columns, kind="recipe.backup"
            )
            if rendered:
                echo(rendered)
            ui.message(
                "info",
                f"would prune {len(pruned)} of {plural(len(bundles), 'backup')}, "
                f"keep {len(bundles) - len(pruned)}",
            )
            return
        rows = [{"id": bundle.id, "size_bytes": sizes[bundle.id]} for bundle, _ in outcome.results]
        rendered = render_rows(rows, fmt=fmt, columns=columns, kind="recipe.backup")
        if rendered:
            echo(rendered)
        if not outcome.any_failed and (outcome.results or not pruned):
            reclaimed = sum(sizes[bundle.id] for bundle, _ in outcome.results)
            kept = len(bundles) - len(outcome.results)
            ui.message(
                "success",
                f"pruned {len(outcome.results)} of {plural(len(bundles), 'backup')}, "
                f"kept {kept}, reclaimed {reclaimed} bytes",
            )
        finish(outcome)
