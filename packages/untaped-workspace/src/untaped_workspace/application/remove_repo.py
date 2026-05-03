"""Use case: remove a repo from a workspace's manifest (and optionally prune the clone)."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from untaped_workspace.domain import Repo, Workspace, WorkspaceManifest
from untaped_workspace.errors import WorkspaceError


class Filesystem(Protocol):
    """Port: side-effecting filesystem operations the use case may invoke."""

    def rmtree(self, path: Path) -> None: ...


class _ManifestStorage(Protocol):
    def read(self, workspace_dir: Path) -> WorkspaceManifest: ...
    def write(self, workspace_dir: Path, manifest: WorkspaceManifest) -> None: ...


class _StatusInspector(Protocol):
    def is_dirty(self, repo_path: Path) -> bool: ...


class RemoveRepo:
    def __init__(
        self,
        manifest_repo: _ManifestStorage,
        *,
        fs: Filesystem,
        status: _StatusInspector | None = None,
    ) -> None:
        self._manifests = manifest_repo
        self._fs = fs
        self._status = status

    def __call__(
        self,
        workspace: Workspace,
        *,
        ident: str,
        prune: bool = False,
    ) -> Repo:
        manifest = self._manifests.read(workspace.path)
        repo = manifest.find_repo(ident)
        if repo is None:
            raise WorkspaceError(f"repo {ident!r} not declared in workspace {workspace.name!r}")

        local = workspace.path / repo.name
        should_prune = prune and local.is_dir()
        if should_prune and self._status is not None and self._status.is_dirty(local):
            raise WorkspaceError(f"refusing to prune {local}: working tree has uncommitted changes")

        manifest.repos = [r for r in manifest.repos if r is not repo]
        self._manifests.write(workspace.path, manifest)

        if should_prune:
            self._fs.rmtree(local)
        return repo
