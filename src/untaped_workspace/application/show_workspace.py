"""Use case: render manifest-level workspace details."""

from __future__ import annotations

from untaped_workspace.application.ports import ManifestReader
from untaped_workspace.domain import Workspace, WorkspaceDetailRow


class ShowWorkspace:
    def __init__(self, manifest_repo: ManifestReader) -> None:
        self._manifests = manifest_repo

    def __call__(self, workspace: Workspace) -> list[WorkspaceDetailRow]:
        manifest = self._manifests.read(workspace.path)
        repo_count = len(manifest.repos)
        base = {
            "workspace": workspace.name,
            "path": str(workspace.path),
            "default_branch": manifest.defaults.branch,
            "repo_count": repo_count,
        }
        if repo_count == 0:
            return [
                WorkspaceDetailRow(
                    **base,
                    repo="",
                    url="",
                    repo_branch=None,
                    target_branch=None,
                )
            ]
        return [
            WorkspaceDetailRow(
                **base,
                repo=repo.name,
                url=repo.url,
                repo_branch=repo.branch,
                target_branch=manifest.target_branch_for(repo),
            )
            for repo in manifest.repos
        ]
