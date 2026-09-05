"""Use cases: update manifest branch metadata without touching git checkouts."""

from __future__ import annotations

from untaped_workspace.application.ports import ManifestRepository
from untaped_workspace.domain import BranchChange, Workspace
from untaped_workspace.errors import WorkspaceError


class SetWorkspaceBranch:
    def __init__(self, manifest_repo: ManifestRepository) -> None:
        self._manifests = manifest_repo

    def __call__(
        self,
        workspace: Workspace,
        *,
        branch: str,
        repo: str | None = None,
    ) -> BranchChange:
        manifest = self._manifests.read(workspace.path)
        if repo is None:
            updated = manifest.with_default_branch(branch)
            changed = BranchChange(workspace=workspace.name, repo=None, branch=branch)
        else:
            try:
                updated, changed_repo = manifest.with_repo_branch(repo, branch)
            except ValueError as exc:
                raise WorkspaceError(
                    f"repo {repo!r} not declared in workspace {workspace.name!r}"
                ) from exc
            changed = BranchChange(
                workspace=workspace.name,
                repo=changed_repo.name,
                branch=branch,
            )
        self._manifests.write(workspace.path, updated)
        return changed


class UnsetWorkspaceBranch:
    def __init__(self, manifest_repo: ManifestRepository) -> None:
        self._manifests = manifest_repo

    def __call__(
        self,
        workspace: Workspace,
        *,
        repo: str | None = None,
    ) -> BranchChange:
        manifest = self._manifests.read(workspace.path)
        if repo is None:
            updated = manifest.with_default_branch(None)
            changed = BranchChange(workspace=workspace.name, repo=None, branch=None)
        else:
            try:
                updated, changed_repo = manifest.with_repo_branch(repo, None)
            except ValueError as exc:
                raise WorkspaceError(
                    f"repo {repo!r} not declared in workspace {workspace.name!r}"
                ) from exc
            changed = BranchChange(workspace=workspace.name, repo=changed_repo.name, branch=None)
        self._manifests.write(workspace.path, updated)
        return changed
