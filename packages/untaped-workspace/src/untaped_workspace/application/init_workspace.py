"""Use case: create a new workspace (manifest + registry entry)."""

from __future__ import annotations

from pathlib import Path

from untaped_workspace.application.workspace_bootstrapper import WorkspaceBootstrapper
from untaped_workspace.domain import (
    ManifestDefaults,
    Workspace,
    WorkspaceManifest,
)


class InitWorkspace:
    def __init__(self, bootstrapper: WorkspaceBootstrapper) -> None:
        self._bootstrap = bootstrapper

    def __call__(
        self,
        path: Path,
        *,
        name: str | None = None,
        branch: str | None = None,
    ) -> Workspace:
        def _build(ws_name: str) -> WorkspaceManifest:
            defaults = ManifestDefaults(branch=branch) if branch else ManifestDefaults()
            return WorkspaceManifest(name=ws_name, defaults=defaults)

        return self._bootstrap(path, build_manifest=_build, name=name)
