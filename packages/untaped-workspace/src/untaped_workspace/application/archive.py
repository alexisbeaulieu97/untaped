"""Archive a workspace: remove its worktrees; keep its folder, rows and holds.

An archived workspace is still a project: its ``.untaped/`` keeps workspace's
rows and the repos it holds, so the repo store keeps them, and ``create``
(or ``add``) resumes it, checking its repos out again on their branches.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.sdk import Failure, UntapedError, command_line, q
from untaped_workspace.errors import WorkspaceError
from untaped_workspace.records import ArchiveOutcome

if TYPE_CHECKING:
    from untaped_workspace.application.ports import GitWorktrees, WorkspaceStore
    from untaped_workspace.domain.models import RepoSpec, WorkspaceRecord


class ArchiveWorkspace:
    """``workspace archive`` once the caller has decided it is safe (or forced).

    The caller holds :meth:`hold` from its safety check to the removal, so no
    ``create``/``add`` of the workspace can land in between.
    Each repo's worktree is removed; a failure becomes a ``failed`` row and
    leaves the workspace active so ``archive`` can be retried. When every
    worktree is gone, the workspace is marked archived; its folder stays.
    """

    def __init__(
        self, store: WorkspaceStore, git: GitWorktrees, *, now: Callable[[], datetime]
    ) -> None:
        self._store = store
        self._git = git
        self._now = now

    @contextmanager
    def hold(self, located: WorkspaceRecord) -> Iterator[WorkspaceRecord]:
        """Hold the workspace's project lock; yield its record as stored now.

        Refused when it is archived (meanwhile, or already).
        """
        with self._store.locked(located) as record:
            if record.archived:
                raise WorkspaceError(
                    f"workspace {q(record.name)} is already archived",
                    category="conflict",
                    hint=resume_hint(record),
                )
            yield record

    def __call__(self, record: WorkspaceRecord, *, force: bool) -> list[ArchiveOutcome]:
        """Archive ``record``, the one :meth:`hold` yielded (the caller holds the lock)."""
        rows = [self._remove(record.name, record.root, spec, force=force) for spec in record.repos]
        if any(row.action == "failed" for row in rows):
            return rows
        if record.meta is not None:
            self._store.set_archived(record, self._now())
        rows.append(
            ArchiveOutcome(
                workspace=record.name,
                repo="",
                action="archived",
                detail=f"kept the folder and its repos' holds; {resume_hint(record)}",
                target_path=record.root,
            )
        )
        return rows

    def _remove(self, workspace: str, root: Path, spec: RepoSpec, *, force: bool) -> ArchiveOutcome:
        dest = root / spec.dir
        try:
            self._git.remove(spec.url, dest, base=spec.base, force=force)
        except UntapedError as exc:
            return ArchiveOutcome(
                workspace=workspace,
                repo=spec.name,
                action="failed",
                detail=str(exc),
                target_path=dest,
                error=Failure.from_exception(exc, message=str(exc)),
            )
        return ArchiveOutcome(
            workspace=workspace, repo=spec.name, action="removed", target_path=dest
        )


def resume_hint(record: WorkspaceRecord) -> str:
    """What resumes an archived workspace: ``create`` for one it made, else ``add``."""
    from untaped_workspace.commands.create import Add, Create  # noqa: PLC0415 - import cycle

    made = record.meta is not None and record.meta.created_by == "workspace"
    command = Create.spelled(record.name) if made else Add.spelled(str(record.root))
    return f"`{command_line(command)}` resumes it"
