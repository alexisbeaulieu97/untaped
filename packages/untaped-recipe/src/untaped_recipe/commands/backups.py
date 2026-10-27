"""``untaped recipe backups ...``: the bundles ``apply`` writes before it changes files."""

from __future__ import annotations

from collections.abc import Iterator

from pydantic import Field

from untaped.sdk import (
    ConfigError,
    Confirm,
    Failure,
    Group,
    Output,
    Positional,
    UntapedError,
    now,
    plural,
    size_text,
)
from untaped_recipe.commands._recipe import RecipeCommand, as_recipe_error, recipe_errors
from untaped_recipe.infrastructure.backup import (
    BackupBundle,
    BackupMetadata,
    bundle_bytes,
    bundle_created_at,
    prune_selection,
    read_metadata,
)
from untaped_recipe.records import Backup, BackupFile, PruneOutcome, RestoreOutcome

#: How a backup id reads in help.
BACKUP_ID = "Backup id, an unambiguous prefix, or latest."


class List(RecipeCommand):
    """List backup bundles.

    A bundle holds text content only, not mode or mtime, and is owner-only
    (directories `0700`, files `0600`).
    """

    output: Output[Backup]

    def run(self) -> Iterator[Backup]:
        with recipe_errors():
            bundles = self.backups().list()
        return iter([self._row(bundle) for bundle in bundles])

    def _row(self, bundle: BackupBundle) -> Backup:
        """One bundle; unreadable metadata warns and leaves ``recipe`` empty."""
        try:
            recipe = read_metadata(bundle).recipe
        except ValueError as exc:
            self.ctx.diag.warning(str(exc))
            recipe = None
        return _backup(bundle, recipe=recipe)


class Get(RecipeCommand):
    """Show a backup bundle: its recipe, inputs and the files it holds."""

    backup_id: Positional[str] = Field(description=BACKUP_ID)
    output: Output[Backup]

    def run(self) -> Backup:
        with recipe_errors():
            bundle = self.backups().resolve(self.backup_id)
            metadata = read_metadata(bundle)
        return _backup(bundle, recipe=metadata.recipe, metadata=metadata)


class Restore(RecipeCommand):
    """Restore a backup bundle.

    The whole bundle is restored as one transaction, after a preview and a
    confirmation. It refuses to overwrite files changed after the backup
    unless --force.

    Examples
    --------
    ```console
    $ untaped recipe backups restore latest --dry-run
    ```
    """

    backup_id: Positional[str] = Field(description=BACKUP_ID)
    force: bool = Field(False, description="Overwrite files changed after the backup.")
    confirm: Confirm
    output: Output[RestoreOutcome]

    def run(self) -> RestoreOutcome:
        with recipe_errors():
            store = self.backups()
            bundle_id = store.resolve(self.backup_id).id
            items = store.plan_restore(bundle_id, force=self.force)
        lines = [f"About to restore {plural(len(items), 'file')}:"]
        lines += [f"  - {item.path}\t{item.action}" for item in items]
        if not self.confirm(f"Restore backup {bundle_id}?", preview=lambda: lines):
            return RestoreOutcome(id=bundle_id, files=len(items), action="planned")
        try:
            # One call: the bundle resolves once and every file flushes in a
            # single staged transaction (all of them, or rolled back).
            with recipe_errors():
                store.restore(bundle_id, force=self.force)
        except UntapedError as exc:
            self.ctx.diag.failure(exc, item=bundle_id)
            error = Failure.from_exception(exc)
            return RestoreOutcome(
                id=bundle_id, files=len(items), action="failed", detail=error.message, error=error
            )
        self.ctx.diag.success(f"restored {bundle_id}")
        return RestoreOutcome(id=bundle_id, files=len(items), action="restored")


class Prune(RecipeCommand):
    """Prune old backup bundles.

    Without --keep or --older-than, the `recipe.backup_keep` and
    `recipe.backup_max_age_days` settings decide.
    """

    keep: int | None = Field(None, ge=1, description="Keep only the newest N bundles.")
    older_than: int | None = Field(None, ge=1, description="Prune bundles older than DAYS days.")
    confirm: Confirm
    output: Output[PruneOutcome]

    def run(self) -> Iterator[PruneOutcome]:
        keep = self.settings.backup_keep if self.keep is None else self.keep
        age = self.settings.backup_max_age_days if self.older_than is None else self.older_than
        if keep is None and age is None:
            raise ConfigError(
                "backups prune needs --keep/--older-than "
                "or backup_keep/backup_max_age_days settings"
            )
        with recipe_errors():
            store = self.backups()
            bundles = store.list()
            pruned = prune_selection(bundles, keep=keep, max_age_days=age, now=now())
        sizes = {bundle.id: bundle_bytes(bundle) for bundle in pruned}
        question = f"Prune {plural(len(pruned), 'backup')} of {len(bundles)}?"
        lines = [f"  - {bundle.id} ({size_text(sizes[bundle.id])})" for bundle in pruned]
        if not self.confirm(question, preview=lambda: lines, needed=bool(pruned)):
            self.ctx.diag.info(
                f"would prune {len(pruned)} of {plural(len(bundles), 'backup')}, "
                f"keep {len(bundles) - len(pruned)}",
            )
            return iter(
                [PruneOutcome(id=b.id, size_bytes=sizes[b.id], action="planned") for b in pruned]
            )
        delete = as_recipe_error(store.delete)
        rows: list[PruneOutcome] = []
        for bundle in pruned:
            try:
                delete(bundle.id)
            except UntapedError as exc:
                self.ctx.diag.failure(exc, item=bundle.id)
                error = Failure.from_exception(exc)
                rows.append(
                    PruneOutcome(
                        id=bundle.id,
                        size_bytes=sizes[bundle.id],
                        action="failed",
                        detail=error.message,
                        error=error,
                    )
                )
            else:
                rows.append(
                    PruneOutcome(id=bundle.id, size_bytes=sizes[bundle.id], action="deleted")
                )
        deleted = [row for row in rows if row.action == "deleted"]
        if len(deleted) == len(rows):
            reclaimed = sum(row.size_bytes for row in deleted)
            self.ctx.diag.success(
                f"pruned {len(deleted)} of {plural(len(bundles), 'backup')}, "
                f"kept {len(bundles) - len(deleted)}, reclaimed {reclaimed} bytes",
            )
        return iter(rows)


def _backup(
    bundle: BackupBundle, *, recipe: str | None, metadata: BackupMetadata | None = None
) -> Backup:
    """One bundle's row; with its ``metadata`` (``backups get``) its inputs and files too."""
    return Backup(
        id=bundle.id,
        created_at=bundle_created_at(bundle),
        recipe=recipe,
        path=str(bundle.path),
        inputs=None if metadata is None else metadata.inputs,
        files=None
        if metadata is None
        else [BackupFile.model_validate(entry.model_dump()) for entry in metadata.files],
    )


class BackupsGroup(Group):
    """Manage recipe backups."""

    commands = (List, Get, Restore, Prune)
