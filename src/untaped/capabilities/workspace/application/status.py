"""Live git status of a workspace's repos, and what would block archiving each one."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from untaped.capabilities.workspace.domain.records import StatusRow
from untaped.capabilities.workspace.domain.safety import archive_blockers

if TYPE_CHECKING:
    from untaped.capabilities.workspace.application.ports import GitWorktrees
    from untaped.capabilities.workspace.domain.models import RepoSpec, WorkspaceRecord


class WorkspaceStatus:
    """``workspace status``: one :class:`StatusRow` per repo, offline unless ``fetch``."""

    def __init__(self, git: GitWorktrees, *, workspaces_dir: Path) -> None:
        self._git = git
        self._workspaces_dir = workspaces_dir

    def __call__(self, record: WorkspaceRecord, *, fetch: bool = False) -> list[StatusRow]:
        if fetch:
            for spec in record.repos:
                self._git.fetch(spec.url)
        root = self._workspaces_dir.expanduser().absolute() / record.name
        return [self._row(record.name, root, spec) for spec in record.repos]

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
            return StatusRow(**common, branch=spec.branch, state="cache_missing")
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
