"""Resolve which workspace a command should act on.

Resolution order: explicit ``name`` (registry lookup) → explicit ``path``
→ walk up from ``cwd`` looking for ``untaped.yml`` → error.
"""

from __future__ import annotations

from pathlib import Path

from untaped_core import ConfigError

from untaped_workspace.domain import Workspace
from untaped_workspace.infrastructure.manifest_repo import MANIFEST_FILENAME
from untaped_workspace.infrastructure.registry_repo import WorkspaceRegistryRepository


class WorkspaceResolver:
    def __init__(self, registry: WorkspaceRegistryRepository | None = None) -> None:
        self._registry = registry or WorkspaceRegistryRepository()

    def resolve(
        self,
        *,
        name: str | None = None,
        path: Path | None = None,
        cwd: Path | None = None,
    ) -> Workspace:
        if name is not None:
            return self._registry.get(name)
        if path is not None:
            return self._resolve_by_path(path)
        return self._resolve_from_cwd(cwd or Path.cwd())

    # internal -----------------------------------------------------------

    def _resolve_by_path(self, path: Path) -> Workspace:
        canonical = path.expanduser().resolve()
        if not (canonical / MANIFEST_FILENAME).is_file():
            raise ConfigError(f"no workspace manifest at {canonical / MANIFEST_FILENAME}")
        existing = self._registry.find_by_path(canonical)
        if existing is not None:
            return existing
        # Unregistered manifest — synthesise a Workspace from the dirname so
        # cwd-discovered workspaces still work for non-registry commands.
        return Workspace(name=canonical.name, path=canonical)

    def _resolve_from_cwd(self, cwd: Path) -> Workspace:
        cwd = cwd.expanduser().resolve()
        for parent in [cwd, *cwd.parents]:
            if (parent / MANIFEST_FILENAME).is_file():
                return self._resolve_by_path(parent)
        raise ConfigError(
            "not inside a workspace — pass --name or --path, or `cd` into a "
            "workspace directory containing untaped.yml"
        )
