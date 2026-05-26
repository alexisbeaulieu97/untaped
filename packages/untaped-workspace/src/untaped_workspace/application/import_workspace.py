"""Use case: import a workspace from a local YAML manifest file."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from untaped_workspace.application.ports import ExternalManifestReader
from untaped_workspace.application.workspace_bootstrapper import WorkspaceBootstrapper
from untaped_workspace.domain import Workspace, WorkspaceManifest


@dataclass(frozen=True)
class ImportResult:
    workspace: Workspace
    # Names of every repo imported from the external manifest, in
    # source order. The CLI's ``--sync`` block feeds this to
    # ``SyncWorkspace(..., only=…)`` so the scope contract matches
    # ``add --sync`` — sync only what this command changed.
    repos: tuple[str, ...]


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
    ) -> ImportResult:
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

        workspace = self._bootstrap(path, build_manifest=_build, name=name or loaded.manifest.name)
        return ImportResult(
            workspace=workspace,
            repos=tuple(r.name for r in loaded.manifest.repos),
        )
