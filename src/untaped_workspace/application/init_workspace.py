"""Use case: create a new workspace (manifest + registry entry)."""

from __future__ import annotations

from pathlib import Path

from untaped_workspace.application.ports import (
    Filesystem,
    ManifestRepository,
    WorkspaceRegistry,
)
from untaped_workspace.domain import (
    ManifestDefaults,
    Workspace,
    WorkspaceManifest,
)
from untaped_workspace.errors import WorkspaceError


class InitWorkspace:
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
        path: Path,
        *,
        name: str | None = None,
        branch: str | None = None,
    ) -> Workspace:
        canonical = path.expanduser().resolve()
        ws_name = name or canonical.name
        if not ws_name:
            raise WorkspaceError(f"unable to derive workspace name from {path}")

        if self._manifests.exists(canonical):
            raise WorkspaceError(f"workspace already initialised at {canonical}")
        if self._registry.find_by_path(canonical) is not None:
            raise WorkspaceError(f"path already registered: {canonical}")

        manifest = WorkspaceManifest(
            name=ws_name,
            defaults=ManifestDefaults(branch=branch) if branch else ManifestDefaults(),
        )
        self._fs.mkdir(canonical, parents=True, exist_ok=True)
        self._manifests.write(canonical, manifest)
        return self._registry.register(name=ws_name, path=canonical)
