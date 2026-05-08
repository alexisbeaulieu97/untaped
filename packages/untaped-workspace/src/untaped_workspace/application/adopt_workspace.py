"""Use case: initialise a workspace from already-cloned repos under a path."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from untaped_workspace.application.ports import (
    DiscoveredRepo,
    ManifestRepository,
    RepoDiscoverer,
    WorkspaceRegistry,
)
from untaped_workspace.domain import (
    ManifestDefaults,
    Repo,
    Workspace,
    WorkspaceManifest,
)
from untaped_workspace.errors import WorkspaceError


@dataclass(frozen=True)
class AdoptResult:
    workspace: Workspace
    repos: list[DiscoveredRepo]


def _noop(_: str) -> None:
    return None


class AdoptWorkspace:
    def __init__(
        self,
        manifest_repo: ManifestRepository,
        registry: WorkspaceRegistry,
        discoverer: RepoDiscoverer,
        *,
        warn: Callable[[str], None] = _noop,
    ) -> None:
        self._manifests = manifest_repo
        self._registry = registry
        self._discoverer = discoverer
        self._warn = warn

    def __call__(
        self,
        path: Path,
        *,
        name: str | None = None,
    ) -> AdoptResult:
        canonical = path.expanduser().resolve()
        if not canonical.exists():
            raise WorkspaceError(f"path does not exist: {canonical}")
        if not canonical.is_dir():
            raise WorkspaceError(f"not a directory: {canonical}")

        ws_name = name or canonical.name
        if not ws_name:
            raise WorkspaceError(f"unable to derive workspace name from {path}")

        if self._manifests.exists(canonical):
            raise WorkspaceError(f"workspace already initialised at {canonical}")
        if self._registry.find_by_path(canonical) is not None:
            raise WorkspaceError(f"path already registered: {canonical}")

        result = self._discoverer.discover(canonical)
        for reason in result.skipped:
            self._warn(reason)
        repos = [Repo(url=d.url, name=d.name, branch=d.branch) for d in result.repos]

        manifest = WorkspaceManifest(
            name=ws_name,
            defaults=ManifestDefaults(),
            repos=repos,
        )
        self._manifests.write(canonical, manifest)
        workspace = self._registry.register(name=ws_name, path=canonical)
        return AdoptResult(workspace=workspace, repos=list(result.repos))
