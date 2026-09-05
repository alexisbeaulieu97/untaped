"""Use case: checkout existing repos to their manifest target branch."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from untaped_workspace.application.ports import BranchOperations, Filesystem, ManifestReader
from untaped_workspace.application.repo_selector import select_repos
from untaped_workspace.domain import (
    BranchApplyAction,
    BranchApplyOutcome,
    Repo,
    Workspace,
    WorkspaceManifest,
)
from untaped_workspace.errors import GitError, UnmatchedRepoFilter


class ApplyWorkspaceBranch:
    def __init__(
        self,
        manifest_repo: ManifestReader,
        git: BranchOperations,
        *,
        fs: Filesystem,
    ) -> None:
        self._manifests = manifest_repo
        self._git = git
        self._fs = fs

    def __call__(
        self,
        workspace: Workspace,
        *,
        repo: Sequence[str] | str | None = None,
    ) -> list[BranchApplyOutcome]:
        manifest = self._manifests.read(workspace.path)
        repos = self._select_repos(manifest, repo=repo)
        return [self._apply_repo(workspace, manifest, target) for target in repos]

    def _select_repos(
        self,
        manifest: WorkspaceManifest,
        *,
        repo: Sequence[str] | str | None,
    ) -> Sequence[Repo]:
        identifiers = (repo,) if isinstance(repo, str) else repo
        repos, unmatched = select_repos(manifest, identifiers)
        if unmatched:
            raise UnmatchedRepoFilter(unmatched)
        return repos

    def _apply_repo(
        self,
        workspace: Workspace,
        manifest: WorkspaceManifest,
        repo: Repo,
    ) -> BranchApplyOutcome:
        target_branch = manifest.target_branch_for(repo)
        local = workspace.path / repo.name
        if target_branch is None:
            return _outcome(workspace, repo, target_branch, "skip", "no target branch")
        if not self._fs.exists(local):
            return _outcome(workspace, repo, target_branch, "skip", "not cloned")
        if (detail := self._try_fetch(local)) is not None:
            return _outcome(workspace, repo, target_branch, "skip", detail)
        try:
            status = self._git.status(local)
        except GitError as exc:
            return _outcome(workspace, repo, target_branch, "skip", f"status failed: {exc}")
        if status.dirty:
            return _outcome(workspace, repo, target_branch, "skip", "dirty working tree")
        if status.diverged:
            return _outcome(workspace, repo, target_branch, "skip", "diverged from origin")
        if status.branch == target_branch:
            return _outcome(
                workspace,
                repo,
                target_branch,
                "up-to-date",
                f"already on {target_branch}",
            )
        try:
            self._git.checkout_branch(local, branch=target_branch)
        except GitError as exc:
            return _outcome(workspace, repo, target_branch, "skip", f"checkout failed: {exc}")
        return _outcome(
            workspace,
            repo,
            target_branch,
            "checkout",
            f"from {status.branch or 'detached'}",
        )

    def _try_fetch(self, repo_path: Path) -> str | None:
        try:
            self._git.fetch(repo_path)
        except GitError as exc:
            return f"fetch failed: {exc}"
        return None


def _outcome(
    workspace: Workspace,
    repo: Repo,
    target_branch: str | None,
    action: BranchApplyAction,
    detail: str,
) -> BranchApplyOutcome:
    return BranchApplyOutcome(
        repo=repo.name,
        workspace=workspace.name,
        target_branch=target_branch,
        action=action,
        detail=detail,
    )
