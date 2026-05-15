"""Use case: import a workspace from a local YAML manifest file."""

from __future__ import annotations

from pathlib import Path

from untaped_workspace.application.ports import ExternalManifestReader
from untaped_workspace.application.workspace_bootstrapper import WorkspaceBootstrapper
from untaped_workspace.domain import Workspace, WorkspaceManifest


class ImportWorkspace:
    def __init__(
        self,
        external_reader: ExternalManifestReader,
        bootstrapper: WorkspaceBootstrapper,
    ) -> None:
        self._external = external_reader
        self._bootstrap = bootstrapper

    def __call__(
        self,
        source: Path,
        *,
        path: Path,
        name: str | None = None,
    ) -> Workspace:
        loaded = self._external.read_external(source.expanduser().resolve())

        # Direct construction (not `model_copy`) keeps this consistent
        # with the manifest mutation contract — see
        # `packages/untaped-workspace/AGENTS.md` "Manifest mutation
        # contract".
        def _build(ws_name: str) -> WorkspaceManifest:
            return WorkspaceManifest(
                name=ws_name,
                defaults=loaded.manifest.defaults,
                repos=loaded.manifest.repos,
            )

        return self._bootstrap(path, build_manifest=_build, name=name or loaded.manifest.name)
