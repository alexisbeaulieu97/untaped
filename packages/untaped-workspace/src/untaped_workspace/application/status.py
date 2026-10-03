"""Live git status of a workspace's repos, and what would block archiving each one."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from untaped.sdk import ErrorInfo, UntapedError, bounded_map, note_failure
from untaped_workspace.application.locate import workspace_root
from untaped_workspace.domain.records import StatusRow
from untaped_workspace.domain.safety import (
    CACHE_MISSING,
    UNREADABLE,
    archive_blockers,
)

if TYPE_CHECKING:
    from untaped_workspace.application.ports import GitWorktrees
    from untaped_workspace.domain.models import RepoSpec, WorkspaceRecord


class WorkspaceStatus:
    """``workspace status``: one :class:`StatusRow` per repo, offline unless ``fetch``.

    Repos are read ``parallel`` at a time; rows keep the record's order.

    A failed fetch does not stop the others: that repo's row still reports
    its local state, with ``detail`` and ``error`` saying why the fetch failed.
    A worktree git cannot read gets an ``error`` row that blocks archiving.
    """

    def __init__(self, git: GitWorktrees, *, workspaces_dir: Path, parallel: int) -> None:
        self._git = git
        self._workspaces_dir = workspaces_dir
        self._parallel = max(1, parallel)

    def __call__(self, record: WorkspaceRecord, *, fetch: bool = False) -> list[StatusRow]:
        root = workspace_root(self._workspaces_dir, record.name)
        rows: dict[int, StatusRow] = {}

        def _one(item: tuple[int, RepoSpec]) -> StatusRow:
            spec = item[1]
            failure = self._fetch(spec.url) if fetch else None
            row = self._row(record.name, root, spec)
            if failure is not None and row.error is None:
                row = row.model_copy(
                    update={"detail": f"fetch failed: {failure.message}", "error": failure}
                )
            return row

        def _collect(item: tuple[int, RepoSpec], row: StatusRow) -> None:
            rows[item[0]] = row

        bounded_map(
            _one, list(enumerate(record.repos)), concurrency=self._parallel, on_each=_collect
        )
        return [rows[i] for i in sorted(rows)]

    def _fetch(self, url: str) -> ErrorInfo | None:
        try:
            self._git.fetch(url)
        except UntapedError as exc:
            return note_failure(exc, message=str(exc))
        return None

    def _row(self, workspace: str, root: Path, spec: RepoSpec) -> StatusRow:
        common = {
            "workspace": workspace,
            "repo": spec.name,
            "dir": spec.dir,
            "base": spec.base,
            "read_only": spec.read_only,
            "target_path": root / spec.dir,
        }
        if not self._git.cache_exists(spec.url):
            return StatusRow(
                **common, branch=spec.branch, state="cache_missing", blockers=(CACHE_MISSING,)
            )
        try:
            status = self._git.status(root / spec.dir, branch=spec.branch)
        except UntapedError as exc:
            reason = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
            return StatusRow(
                **common,
                branch=spec.branch,
                state="error",
                blockers=(f"{UNREADABLE}: {reason}",),
                detail=str(exc),
                error=note_failure(exc, message=str(exc)),
            )
        if status is None:
            return StatusRow(**common, branch=spec.branch, state="missing")
        return StatusRow(
            **common,
            branch=status.branch,
            state="ok",
            upstream=status.upstream,
            ahead=status.ahead,
            behind=status.behind,
            modified=status.modified,
            untracked=status.untracked,
            stashed=status.stashed,
            unpushed=status.unpushed,
            blockers=archive_blockers(status),
        )
