"""Archive a workspace: remove its worktrees and directory, keep its record as archived."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.sdk import UntapedError, note_failure
from untaped_workspace.application.locate import active_workspace, workspace_root
from untaped_workspace.domain.records import ArchiveOutcome

if TYPE_CHECKING:
    from untaped_workspace.application.ports import GitWorktrees, WorkspaceStore
    from untaped_workspace.domain.models import RepoSpec, WorkspaceRecord


class ArchiveWorkspace:
    """``workspace archive`` once the caller has decided it is safe (or forced).

    The caller holds :meth:`hold` from its safety check to the removal, so no
    ``create``/``add`` of the workspace can land in between.
    Each repo's worktree is removed; a failure becomes a ``failed`` row and
    leaves the workspace active so ``archive`` can be retried. When every repo
    is gone, the directory is deleted if empty and the record moves to the
    archived list; a directory with other files in it stays, as a
    ``skipped`` workspace row.
    """

    def __init__(
        self,
        store: WorkspaceStore,
        git: GitWorktrees,
        *,
        workspaces_dir: Path,
        now: Callable[[], datetime],
    ) -> None:
        self._store = store
        self._git = git
        self._workspaces_dir = workspaces_dir
        self._now = now

    @contextmanager
    def hold(self, name: str) -> Iterator[WorkspaceRecord]:
        """Hold workspace ``name``'s lock; yield its record as stored now.

        Not found when it was archived meanwhile; repos an ``add`` recorded
        before the lock was taken are in the yielded record.
        """
        with self._store.locked(name):
            yield active_workspace(self._store, name)

    def __call__(self, record: WorkspaceRecord, *, force: bool) -> list[ArchiveOutcome]:
        """Archive ``record``, the one :meth:`hold` yielded (the caller holds the lock)."""
        root = workspace_root(self._workspaces_dir, record.name)
        rows = [self._remove(record.name, root, spec, force=force) for spec in record.repos]
        if any(row.action == "failed" for row in rows):
            return rows
        emptied = _remove_if_empty(root)
        rows.append(
            ArchiveOutcome(
                workspace=record.name,
                repo="",
                action="removed" if emptied else "skipped",
                detail="workspace directory" if emptied else f"left other files in {root}",
                target_path=root,
            )
        )
        self._store.archive(record.name, at=self._now())
        return rows

    def _remove(self, workspace: str, root: Path, spec: RepoSpec, *, force: bool) -> ArchiveOutcome:
        dest = root / spec.dir
        try:
            self._git.remove(spec.url, dest, force=force)
        except UntapedError as exc:
            return ArchiveOutcome(
                workspace=workspace,
                repo=spec.name,
                action="failed",
                detail=str(exc),
                target_path=dest,
                error=note_failure(exc, message=str(exc)),
            )
        return ArchiveOutcome(
            workspace=workspace, repo=spec.name, action="removed", target_path=dest
        )


def _remove_if_empty(path: Path) -> bool:
    """Delete ``path`` when it is empty (or already gone); ``False`` when files remain."""
    try:
        path.rmdir()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True
