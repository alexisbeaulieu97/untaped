"""Archive a workspace: remove its worktrees and directory, keep its record as archived."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.capabilities.workspace.domain.records import ArchiveOutcome
from untaped.capability_api import UntapedError, note_failure

if TYPE_CHECKING:
    from untaped.capabilities.workspace.application.ports import GitWorktrees, WorkspaceStore
    from untaped.capabilities.workspace.domain.models import RepoSpec, WorkspaceRecord


class ArchiveWorkspace:
    """``workspace archive`` once the caller has decided it is safe (or forced).

    Each repo's worktree is removed; a failure becomes a ``failed`` row and
    leaves the workspace active so ``archive`` can be retried. When every repo
    is gone, the directory is deleted if empty and the record moves to the
    archived list.
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

    def __call__(self, record: WorkspaceRecord, *, force: bool) -> list[ArchiveOutcome]:
        root = self._workspaces_dir.expanduser().absolute() / record.name
        rows = [self._remove(record.name, root, spec, force=force) for spec in record.repos]
        if any(row.action == "failed" for row in rows):
            return rows
        detail = "workspace directory" if _remove_if_empty(root) else f"left other files in {root}"
        rows.append(
            ArchiveOutcome(
                workspace=record.name, repo="", action="removed", detail=detail, target_path=root
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
