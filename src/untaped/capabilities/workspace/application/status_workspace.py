"""Use case: collect a `git status` snapshot for every repo in a workspace."""

from __future__ import annotations

from collections.abc import Sequence

from untaped.capabilities.workspace.application.ports import (
    Filesystem,
    GitInspector,
    ManifestReader,
)
from untaped.capabilities.workspace.application.repo_selector import select_repos
from untaped.capabilities.workspace.application.sync_workspace import NOT_A_GIT_REPOSITORY
from untaped.capabilities.workspace.domain import (
    Repo,
    StatusEntry,
    Workspace,
)
from untaped.capabilities.workspace.errors import GitError, ManifestError, UnmatchedRepoFilterError


class WorkspaceStatus:
    def __init__(
        self,
        manifests: ManifestReader,
        git: GitInspector,
        *,
        fs: Filesystem,
    ) -> None:
        self._manifests = manifests
        self._git = git
        self._fs = fs

    def __call__(
        self,
        workspace: Workspace,
        *,
        only: Sequence[str] | None = None,
        skip_manifest_errors: bool = False,
    ) -> list[StatusEntry]:
        try:
            manifest = self._manifests.read(workspace.path)
        except ManifestError as exc:
            if skip_manifest_errors:
                return [_unavailable_row(workspace, exc)]
            raise
        repos, unmatched = select_repos(manifest, only)
        if unmatched:
            raise UnmatchedRepoFilterError(unmatched)
        return [self._row_for(workspace, repo) for repo in repos]

    def _row_for(self, workspace: Workspace, repo: Repo) -> StatusEntry:
        local = workspace.path / repo.name
        if not self._fs.is_dir(local):
            return StatusEntry(workspace=workspace.name, repo=repo.name, cloned=False)
        if not self._fs.exists(local / ".git"):
            return StatusEntry(
                workspace=workspace.name,
                repo=repo.name,
                cloned=False,
                detail=NOT_A_GIT_REPOSITORY,
            )
        try:
            status = self._git.status(local)
        except GitError as exc:
            # The clone exists; surface why it could not be inspected
            # rather than pretending it is missing.
            return StatusEntry(
                workspace=workspace.name,
                repo=repo.name,
                cloned=True,
                detail=f"status failed: {exc}",
            )
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


def _unavailable_row(workspace: Workspace, exc: ManifestError) -> StatusEntry:
    return StatusEntry(
        workspace=workspace.name,
        repo="",
        action="unavailable",
        detail=f"workspace manifest unavailable: {exc}",
        cloned=False,
    )
