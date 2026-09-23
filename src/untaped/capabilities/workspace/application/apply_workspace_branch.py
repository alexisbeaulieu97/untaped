"""Use case: checkout existing repos to their manifest target branch."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from untaped.capabilities.workspace.application.ports import (
    BranchOperations,
    Filesystem,
    ManifestReader,
)
from untaped.capabilities.workspace.application.repo_selector import select_repos
from untaped.capabilities.workspace.application.sync_workspace import NOT_A_GIT_REPOSITORY
from untaped.capabilities.workspace.domain import (
    BranchApplyAction,
    BranchApplyOutcome,
    Repo,
    Workspace,
    WorkspaceManifest,
)
from untaped.capabilities.workspace.errors import GitError, UnmatchedRepoFilterError


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
        create: bool = False,
    ) -> list[BranchApplyOutcome]:
        """Checkout selected repos to their manifest target branch.

        A target that exists neither locally nor as ``origin/<branch>``
        is skipped unless ``create`` is set, so a typo in the manifest
        (``mian``) cannot silently create a branch in every repo.
        """
        manifest = self._manifests.read(workspace.path)
        repos = self._select_repos(manifest, repo=repo)
        return [self._apply_repo(workspace, manifest, target, create=create) for target in repos]

    def _select_repos(
        self,
        manifest: WorkspaceManifest,
        *,
        repo: Sequence[str] | str | None,
    ) -> Sequence[Repo]:
        identifiers = (repo,) if isinstance(repo, str) else repo
        repos, unmatched = select_repos(manifest, identifiers)
        if unmatched:
            raise UnmatchedRepoFilterError(unmatched)
        return repos

    def _apply_repo(
        self,
        workspace: Workspace,
        manifest: WorkspaceManifest,
        repo: Repo,
        *,
        create: bool,
    ) -> BranchApplyOutcome:
        target_branch = manifest.target_branch_for(repo)
        local = workspace.path / repo.name
        if target_branch is None:
            return _outcome(workspace, repo, target_branch, "skip", "no target branch")
        if not self._fs.exists(local):
            return _outcome(workspace, repo, target_branch, "skip", "not cloned")
        if not self._fs.exists(local / ".git"):
            return _outcome(workspace, repo, target_branch, "skip", NOT_A_GIT_REPOSITORY)
        if (detail := self._try_fetch(local)) is not None:
            return _outcome(workspace, repo, target_branch, "failed", detail)
        try:
            status = self._git.status(local)
        except GitError as exc:
            return _outcome(workspace, repo, target_branch, "failed", f"status failed: {exc}")
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
        if not create and not self._git.has_branch(local, branch=target_branch):
            return _outcome(
                workspace,
                repo,
                target_branch,
                "skip",
                "branch not found locally or on origin",
            )
        try:
            self._git.checkout_branch(local, branch=target_branch)
        except GitError as exc:
            return _outcome(workspace, repo, target_branch, "failed", f"checkout failed: {exc}")
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
