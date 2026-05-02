"""Use case: append a repo to a workspace's manifest."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from untaped_workspace.domain import Repo, Workspace, WorkspaceManifest
from untaped_workspace.errors import WorkspaceError


class _ManifestStorage(Protocol):
    def read(self, workspace_dir: Path) -> WorkspaceManifest: ...
    def write(self, workspace_dir: Path, manifest: WorkspaceManifest) -> None: ...


class AddRepo:
    def __init__(self, manifest_repo: _ManifestStorage) -> None:
        self._manifests = manifest_repo

    def __call__(
        self,
        workspace: Workspace,
        *,
        url: str,
        repo_name: str | None = None,
        branch: str | None = None,
    ) -> Repo:
        manifest = self._manifests.read(workspace.path)
        if manifest.repo_by_url(url) is not None:
            raise WorkspaceError(f"repo already in workspace {workspace.name!r}: {url}")
        repo = Repo.model_validate({"url": url, "name": repo_name, "branch": branch})
        existing = manifest.repo_by_name(repo.name)
        if existing is not None:
            # The check has to happen before the in-place ``repos.append`` —
            # the Pydantic ``WorkspaceManifest`` model validator only fires
            # on construction, not on list mutation, so without this guard
            # an explicit ``--repo-name`` collision lands a duplicate on
            # disk that the next read rejects.
            base = (
                f"repo name {repo.name!r} already in use in workspace "
                f"{workspace.name!r} by {existing.url}"
            )
            # ``not repo_name`` mirrors ``Repo._fill_default_name``'s check
            # so an explicit empty string is treated the same as omitting
            # the flag (both produce a derived name).
            if not repo_name:
                raise WorkspaceError(f"{base}; pass --repo-name to disambiguate")
            raise WorkspaceError(base)
        manifest.repos.append(repo)
        self._manifests.write(workspace.path, manifest)
        return repo
