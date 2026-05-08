"""Use case: initialise a workspace from already-cloned repos under a path."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from untaped_workspace.domain import (
    ManifestDefaults,
    Repo,
    Workspace,
    WorkspaceManifest,
)
from untaped_workspace.errors import WorkspaceError


@dataclass(frozen=True)
class DiscoveredRepo:
    """A clone discovered on disk during adopt."""

    name: str
    url: str
    branch: str | None


@dataclass(frozen=True)
class DiscoveryResult:
    """What a :class:`_RepoDiscoverer` returns: kept repos plus skipped reasons."""

    repos: list[DiscoveredRepo]
    skipped: list[str]


class _ManifestStorage(Protocol):
    def exists(self, workspace_dir: Path) -> bool: ...
    def write(self, workspace_dir: Path, manifest: WorkspaceManifest) -> None: ...


class _RegistryStorage(Protocol):
    def register(self, *, name: str, path: Path) -> Workspace: ...
    def find_by_path(self, path: Path) -> Workspace | None: ...


class _RepoDiscoverer(Protocol):
    def discover(self, path: Path) -> DiscoveryResult: ...


@dataclass(frozen=True)
class AdoptResult:
    workspace: Workspace
    repos: list[DiscoveredRepo]


def _noop(_: str) -> None:
    return None


class AdoptWorkspace:
    def __init__(
        self,
        manifest_repo: _ManifestStorage,
        registry: _RegistryStorage,
        discoverer: _RepoDiscoverer,
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
