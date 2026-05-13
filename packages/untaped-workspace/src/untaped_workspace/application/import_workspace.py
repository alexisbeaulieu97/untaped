"""Use case: import a workspace from a local YAML manifest file."""

from __future__ import annotations

from pathlib import Path

from untaped_workspace.application.ports import (
    Filesystem,
    ManifestRepository,
    WorkspaceRegistry,
)
from untaped_workspace.domain import Workspace
from untaped_workspace.errors import WorkspaceError


class ImportWorkspace:
    def __init__(
        self,
        manifest_repo: ManifestRepository,
        registry: WorkspaceRegistry,
        *,
        fs: Filesystem,
    ) -> None:
        self._manifests = manifest_repo
        self._registry = registry
        self._fs = fs

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

        self._fs.mkdir(canonical)
        self._manifests.write(canonical, manifest)
        return self._registry.register(name=ws_name, path=canonical)
