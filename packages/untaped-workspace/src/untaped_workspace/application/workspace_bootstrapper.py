"""Use case: shared opening for every workspace-lifecycle entry point.

``InitWorkspace`` / ``AdoptWorkspace`` / ``ImportWorkspace`` each
delegate the canonicalise → derive-name → collision-guard →
``mkdir`` → ``manifests.write`` → ``registry.register`` scaffold to
this class. Callers vary only in how their ``WorkspaceManifest`` is
built, so they pass a ``build_manifest(ws_name) -> WorkspaceManifest``
closure.

``verify`` is the read-only subset of ``__call__`` — exposed so
callers with expensive pre-bootstrap work (e.g. ``AdoptWorkspace``'s
N x 2 ``git`` subprocess walk in ``LocalRepoDiscoverer``) can fail
fast on collision instead of paying for the walk first.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from untaped_workspace.application.ports import (
    Filesystem,
    ManifestRepository,
    WorkspaceRegistry,
)
from untaped_workspace.domain import Workspace, WorkspaceManifest
from untaped_workspace.errors import WorkspaceError


class WorkspaceBootstrapper:
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

    def _resolve_and_check(self, path: Path, name: str | None) -> tuple[Path, str]:
        canonical = path.expanduser().resolve()
        ws_name = name or canonical.name
        if not ws_name:
            raise WorkspaceError(f"unable to derive workspace name from {path}")
        if self._manifests.exists(canonical):
            raise WorkspaceError(f"workspace already initialised at {canonical}")
        if self._registry.find_by_path(canonical) is not None:
            raise WorkspaceError(f"path already registered: {canonical}")
        return canonical, ws_name

    def verify(self, path: Path, *, name: str | None = None) -> None:
        """Raise if ``path`` cannot become a new workspace.

        Pure read; no mutation. ``__call__`` re-runs the same checks
        before mutating, so this is a fail-fast hint for callers, not
        a TOCTOU guarantee — fine for the single-user CLI today.
        """
        self._resolve_and_check(path, name)

    def __call__(
        self,
        path: Path,
        *,
        build_manifest: Callable[[str], WorkspaceManifest],
        name: str | None = None,
    ) -> Workspace:
        canonical, ws_name = self._resolve_and_check(path, name)
        manifest = build_manifest(ws_name)
        self._fs.mkdir(canonical, parents=True, exist_ok=True)
        self._manifests.write(canonical, manifest)
        return self._registry.register(name=ws_name, path=canonical)
