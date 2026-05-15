"""Use case: initialise a workspace from already-cloned repos under a path."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from untaped_workspace.application.ports import (
    Filesystem,
    RepoDiscoverer,
)
from untaped_workspace.application.workspace_bootstrapper import WorkspaceBootstrapper
from untaped_workspace.domain import (
    DiscoveredRepo,
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
        bootstrapper: WorkspaceBootstrapper,
        discoverer: RepoDiscoverer,
        *,
        fs: Filesystem,
        warn: Callable[[str], None] = _noop,
    ) -> None:
        self._bootstrap = bootstrapper
        self._discoverer = discoverer
        self._fs = fs
        self._warn = warn

    def __call__(
        self,
        path: Path,
        *,
        name: str | None = None,
    ) -> AdoptResult:
        canonical = path.expanduser().resolve()
        if not self._fs.exists(canonical):
            raise WorkspaceError(f"path does not exist: {canonical}")
        if not self._fs.is_dir(canonical):
            raise WorkspaceError(f"not a directory: {canonical}")

        # Fail fast before discovery — discoverer.discover() does an
        # iterdir plus 2 git subprocess spawns per child directory.
        self._bootstrap.verify(path, name=name)

        result = self._discoverer.discover(canonical)
        for reason in result.skipped:
            self._warn(reason)
        repos = [Repo(url=d.url, name=d.name, branch=d.branch) for d in result.repos]

        def _build(ws_name: str) -> WorkspaceManifest:
            return WorkspaceManifest(name=ws_name, defaults=ManifestDefaults(), repos=repos)

        workspace = self._bootstrap(path, build_manifest=_build, name=name)
        return AdoptResult(workspace=workspace, repos=list(result.repos))
