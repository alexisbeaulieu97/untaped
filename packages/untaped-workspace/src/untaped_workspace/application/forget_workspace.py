"""Use case: remove a workspace from the registry (optionally pruning files)."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from untaped_workspace.application.remove_repo import Filesystem, StatusInspector
from untaped_workspace.domain import Workspace, WorkspaceManifest
from untaped_workspace.errors import GitError, WorkspaceError


class _ManifestStorage(Protocol):
    def exists(self, workspace_dir: Path) -> bool: ...
    def read(self, workspace_dir: Path) -> WorkspaceManifest: ...


class _RegistryStorage(Protocol):
    def get(self, name: str) -> Workspace: ...
    def unregister(self, name: str) -> bool: ...


class ForgetWorkspace:
    """Forget a workspace's registry entry; with ``prune=True`` also remove its files.

    Pruning is refused when any declared repo has a dirty working tree —
    same safety check as ``RemoveRepo`` for ``--prune``. Missing manifest
    or missing workspace directory are tolerated (the registry entry is
    still removed).
    """

    def __init__(
        self,
        registry: _RegistryStorage,
        manifest_repo: _ManifestStorage,
        *,
        fs: Filesystem,
        status: StatusInspector,
    ) -> None:
        self._registry = registry
        self._manifests = manifest_repo
        self._fs = fs
        self._status = status

    def __call__(self, name: str, *, prune: bool = False) -> Workspace:
        ws = self._registry.get(name)

        if prune and ws.path.is_dir():
            self._refuse_if_any_repo_dirty(ws)
            self._fs.rmtree(ws.path)

        self._registry.unregister(name)
        return ws

    def _refuse_if_any_repo_dirty(self, ws: Workspace) -> None:
        if not self._manifests.exists(ws.path):
            raise WorkspaceError(
                f"refusing to prune {ws.name!r}: no manifest at {ws.path} "
                "(delete the directory manually if that's what you want)"
            )
        manifest = self._manifests.read(ws.path)
        dirty: list[str] = []
        for repo in manifest.repos:
            local = ws.path / repo.name
            if not local.is_dir():
                continue
            try:
                if self._status.is_dirty(local):
                    dirty.append(repo.name)
            except GitError as exc:
                raise WorkspaceError(
                    f"refusing to prune {ws.name!r}: cannot inspect {repo.name!r} ({local}): {exc}"
                ) from exc
        if dirty:
            raise WorkspaceError(
                f"refusing to prune {ws.name!r}: uncommitted changes in {', '.join(dirty)}"
            )
