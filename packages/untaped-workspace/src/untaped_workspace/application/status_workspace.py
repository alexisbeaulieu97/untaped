"""Use case: collect a `git status` snapshot for every repo in a workspace."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from untaped_workspace.domain import (
    Repo,
    RepoStatus,
    StatusEntry,
    Workspace,
    WorkspaceManifest,
)
from untaped_workspace.errors import GitError


class _ManifestReader(Protocol):
    def read(self, workspace_dir: Path) -> WorkspaceManifest: ...


class _Git(Protocol):
    def status(self, repo_path: Path) -> RepoStatus: ...


class WorkspaceStatus:
    def __init__(self, manifests: _ManifestReader, git: _Git) -> None:
        self._manifests = manifests
        self._git = git

    def __call__(self, workspace: Workspace) -> list[StatusEntry]:
        manifest = self._manifests.read(workspace.path)
        return [self._row_for(workspace, repo) for repo in manifest.repos]

    def _row_for(self, workspace: Workspace, repo: Repo) -> StatusEntry:
        local = workspace.path / repo.name
        if not local.is_dir():
            return StatusEntry(workspace=workspace.name, repo=repo.name, cloned=False)
        try:
            status = self._git.status(local)
        except GitError:
            return StatusEntry(workspace=workspace.name, repo=repo.name, cloned=False)
        return StatusEntry(
            workspace=workspace.name,
            repo=repo.name,
            cloned=True,
            branch=status.branch,
            ahead=status.ahead,
            behind=status.behind,
            modified=status.modified,
            untracked=status.untracked,
        )
