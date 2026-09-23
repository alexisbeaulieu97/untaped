"""Use case: create a new workspace (manifest + registry entry)."""

from __future__ import annotations

from pathlib import Path

from untaped.capabilities.workspace.application.workspace_bootstrapper import WorkspaceBootstrapper
from untaped.capabilities.workspace.domain import (
    ManifestDefaults,
    Workspace,
    WorkspaceManifest,
    check_path_segment,
)
from untaped.capabilities.workspace.errors import WorkspaceError


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
        if name is not None:
            # The CLI's default location is ``<workspaces_dir>/<name>``, so
            # the name must stay a single safe path segment.
            try:
                check_path_segment(name, kind="workspace name")
            except ValueError as exc:
                raise WorkspaceError(str(exc)) from exc

        def _build(ws_name: str) -> WorkspaceManifest:
            defaults = ManifestDefaults(branch=branch) if branch else ManifestDefaults()
            return WorkspaceManifest(name=ws_name, defaults=defaults)

        return self._bootstrap(path, build_manifest=_build, name=name)
