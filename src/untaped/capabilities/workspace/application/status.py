"""Live git status of a workspace's repos, and what would block archiving each one."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from untaped.capabilities.workspace.domain.records import StatusRow
from untaped.capabilities.workspace.domain.safety import archive_blockers
from untaped.capability_api import ErrorInfo, UntapedError, note_failure

if TYPE_CHECKING:
    from untaped.capabilities.workspace.application.ports import GitWorktrees
    from untaped.capabilities.workspace.domain.models import RepoSpec, WorkspaceRecord


CACHE_MISSING = "repo cache missing; local work cannot be checked"
"""Blocker for a repo whose cache is gone: its worktree's state is unknown."""


class WorkspaceStatus:
    """``workspace status``: one :class:`StatusRow` per repo, offline unless ``fetch``.

    A failed fetch does not stop the others: that repo's row still reports
    its local state, with ``detail`` and ``error`` saying why the fetch failed.
    """

    def __init__(self, git: GitWorktrees, *, workspaces_dir: Path) -> None:
        self._git = git
        self._workspaces_dir = workspaces_dir

    def __call__(self, record: WorkspaceRecord, *, fetch: bool = False) -> list[StatusRow]:
        root = self._workspaces_dir.expanduser().absolute() / record.name
        rows: list[StatusRow] = []
        for spec in record.repos:
            failure = self._fetch(spec.url) if fetch else None
            row = self._row(record.name, root, spec)
            if failure is not None:
                row = row.model_copy(
                    update={"detail": f"fetch failed: {failure.message}", "error": failure}
                )
            rows.append(row)
        return rows

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
        status = self._git.status(root / spec.dir, branch=spec.branch, base=spec.base)
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
            blockers=archive_blockers(status, read_only=spec.read_only),
        )
