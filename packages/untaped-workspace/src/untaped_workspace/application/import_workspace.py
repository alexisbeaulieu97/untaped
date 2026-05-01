"""Use case: import a workspace from a local YAML manifest file."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from untaped_workspace.domain import Workspace, WorkspaceManifest
from untaped_workspace.errors import WorkspaceError


class _ManifestStorage(Protocol):
    def exists(self, workspace_dir: Path) -> bool: ...
    def write(self, workspace_dir: Path, manifest: WorkspaceManifest) -> None: ...
    def read_external(self, source: Path) -> _Loaded: ...


class _Loaded(Protocol):
    @property
    def manifest(self) -> WorkspaceManifest: ...
    @property
    def source(self) -> Path: ...


class _RegistryStorage(Protocol):
    def register(self, *, name: str, path: Path) -> Workspace: ...
    def find_by_path(self, path: Path) -> Workspace | None: ...


class ImportWorkspace:
    def __init__(
        self,
        manifest_repo: _ManifestStorage,
        registry: _RegistryStorage,
    ) -> None:
        self._manifests = manifest_repo
        self._registry = registry

    def __call__(
        self,
        source: Path,
        *,
        path: Path,
        name: str | None = None,
    ) -> Workspace:
        loaded = self._manifests.read_external(source.expanduser().resolve())
        canonical = path.expanduser().resolve()

        ws_name = name or loaded.manifest.name or canonical.name
        if not ws_name:
            raise WorkspaceError(f"unable to derive workspace name (path={canonical})")

        if self._manifests.exists(canonical):
            raise WorkspaceError(f"workspace already initialised at {canonical}")
        if self._registry.find_by_path(canonical) is not None:
            raise WorkspaceError(f"path already registered: {canonical}")

        manifest = loaded.manifest.model_copy(update={"name": ws_name})

        canonical.mkdir(parents=True, exist_ok=True)
        self._manifests.write(canonical, manifest)
        return self._registry.register(name=ws_name, path=canonical)
