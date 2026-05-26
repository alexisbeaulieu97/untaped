"""Resolve which workspace a command should act on.

Resolution order: explicit ``name`` (registry lookup) → explicit ``path``
→ walk up from ``cwd`` looking for a workspace manifest → error.

Lives in ``application/`` because *how to name a workspace* is the
package's ubiquitous language — every target-resolving command
(``add``, ``remove``, ``sync``, ``status``, ``foreach``) inherits the
same precedence. The resolver speaks only to its
:class:`untaped_workspace.application.ports.RegistryReader` and
:class:`untaped_workspace.application.ports.ManifestReader` ports;
the CLI composition root wires the concrete repositories.
"""

from __future__ import annotations

from pathlib import Path

from untaped_core import ConfigError

from untaped_workspace.application.ports import ManifestReader, RegistryReader
from untaped_workspace.domain import Workspace


class WorkspaceResolver:
    def __init__(self, *, registry: RegistryReader, manifests: ManifestReader) -> None:
        self._registry = registry
        self._manifests = manifests

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
        if not self._manifests.exists(canonical):
            raise ConfigError(f"no workspace manifest at {canonical}/untaped.yml")
        return self._workspace_for(canonical)

    def _resolve_from_cwd(self, cwd: Path) -> Workspace:
        cwd = cwd.expanduser().resolve()
        for parent in [cwd, *cwd.parents]:
            if self._manifests.exists(parent):
                # `parent` is already canonical (derived from `cwd.resolve()`)
                # and we've just confirmed the manifest — skip the
                # `_resolve_by_path` re-check + re-resolve.
                return self._workspace_for(parent)
        raise ConfigError(
            "not inside a workspace — pass --name or --path, or `cd` into a "
            "workspace directory containing untaped.yml"
        )

    def _workspace_for(self, canonical: Path) -> Workspace:
        existing = self._registry.find_by_path(canonical)
        if existing is not None:
            return existing
        # Unregistered manifest — manifest name is the documented source of
        # truth (see AGENTS.md, "Manifest mutation contract"); without this
        # precedence, `[<dirname>]` would silently shadow a hand-set `name:`
        # in `status`/`foreach` output.
        manifest = self._manifests.read(canonical)
        return Workspace(name=manifest.name or canonical.name, path=canonical)
