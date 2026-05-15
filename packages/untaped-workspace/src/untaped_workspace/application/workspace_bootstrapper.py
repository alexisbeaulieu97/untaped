"""Use case: shared opening for every workspace-lifecycle entry point.

``InitWorkspace`` / ``AdoptWorkspace`` / ``ImportWorkspace`` each
delegate the canonicalise → derive-name → collision-guard →
``manifests.write`` → ``registry.register`` scaffold to this class.
Callers vary only in how their ``WorkspaceManifest`` is built, so they
pass a ``build_manifest(ws_name) -> WorkspaceManifest`` closure.

Workspace-dir creation is a side effect of ``ManifestRepository.write``
— it mkdirs the manifest's parent (which *is* the workspace dir) so
callers don't need to. See ``untaped-workspace/AGENTS.md``.

Two entry points: ``__call__`` for ``InitWorkspace`` /
``ImportWorkspace`` (resolves once, derives name, writes, registers);
``verify`` + ``bootstrap`` for ``AdoptWorkspace`` — the canonical-in
fast path that would otherwise canonicalise three times per
invocation. See ``packages/untaped-workspace/AGENTS.md``'s "`init`
vs. `adopt` vs. `import` vs. `forget`" section for the long form.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from untaped_workspace.application.ports import ManifestRepository, WorkspaceRegistry
from untaped_workspace.domain import Workspace, WorkspaceManifest
from untaped_workspace.errors import WorkspaceError


class WorkspaceBootstrapper:
    def __init__(
        self,
        manifest_repo: ManifestRepository,
        registry: WorkspaceRegistry,
    ) -> None:
        self._manifests = manifest_repo
        self._registry = registry

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

    def verify(self, path: Path, *, name: str | None = None) -> tuple[Path, str]:
        """Resolve ``path``, raise on collision, return ``(canonical, ws_name)``.

        Pairs with :meth:`bootstrap`: the collision check happens here
        and is the only one — calling ``bootstrap`` without ``verify``
        writes/registers without checking for an existing workspace at
        the same path. The TOCTOU window between the two is acceptable
        for the single-user CLI today.
        """
        return self._resolve_and_check(path, name)

    def bootstrap(
        self,
        canonical: Path,
        ws_name: str,
        manifest: WorkspaceManifest,
    ) -> Workspace:
        """Write ``manifest`` at ``canonical`` and register ``ws_name``.

        Precondition: ``canonical`` + ``ws_name`` come from a prior
        :meth:`verify` call. See ``verify`` for the consequence of
        skipping it.
        """
        self._manifests.write(canonical, manifest)
        return self._registry.register(name=ws_name, path=canonical)

    def __call__(
        self,
        path: Path,
        *,
        build_manifest: Callable[[str], WorkspaceManifest],
        name: str | None = None,
    ) -> Workspace:
        canonical, ws_name = self._resolve_and_check(path, name)
        return self.bootstrap(canonical, ws_name, build_manifest(ws_name))
