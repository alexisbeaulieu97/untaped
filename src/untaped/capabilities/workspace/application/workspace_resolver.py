"""Resolve which workspace a command should act on.

Every target-resolving command takes one optional ``WS`` argument, resolved
here: omitted → walk up from ``cwd`` to the nearest workspace manifest; a
path (``.``, ``..``, anything containing a path separator, or ``~``-prefixed)
→ must exist, then walk up from it; anything else → registry lookup by name.

Lives in ``application/`` because *how to name a workspace* is the
package's ubiquitous language — every target-resolving command
(``repos``, ``sync``, ``status``, ``foreach``, ``branch``, ``edit``) inherits
the same rule. The resolver speaks only to its
:class:`untaped.capabilities.workspace.application.ports.RegistryReader` and
:class:`untaped.capabilities.workspace.application.ports.ManifestReader` ports;
the CLI composition root wires the concrete repositories.
"""

from __future__ import annotations

import os
from pathlib import Path

from untaped.capabilities.workspace.application.ports import (
    Filesystem,
    ManifestReader,
    RegistryReader,
)
from untaped.capabilities.workspace.domain import Workspace
from untaped.capability_api import ConfigError


def _looks_like_path(target: str) -> bool:
    """Whether a ``WS`` argument names a directory rather than a registry name.

    Workspace names are single path segments, so a separator, ``.``/``..``
    or a leading ``~`` can only mean a path.
    """
    separators = {os.sep, "/"} | ({os.altsep} if os.altsep else set())
    return target in {".", ".."} or target.startswith("~") or any(s in target for s in separators)


class WorkspaceResolver:
    def __init__(
        self, *, registry: RegistryReader, manifests: ManifestReader, fs: Filesystem
    ) -> None:
        self._registry = registry
        self._manifests = manifests
        self._fs = fs

    def resolve(self, target: str | None = None, *, cwd: Path | None = None) -> Workspace:
        base = cwd or Path.cwd()
        if target is None:
            return self._resolve_from(
                base,
                error=(
                    "not inside a workspace — pass a workspace name or path, or `cd` "
                    "into a workspace directory containing untaped.yml"
                ),
            )
        if _looks_like_path(target):
            try:
                start = (base / Path(target).expanduser()).resolve()
            except RuntimeError as exc:  # ``~user`` for an unknown user
                raise ConfigError(f"cannot expand workspace path {target!r}: {exc}") from exc
            if not self._fs.exists(start):
                # A typo must not silently walk up to an enclosing workspace.
                raise ConfigError(f"workspace path does not exist: {start}")
            return self._resolve_from(
                start, error=f"no workspace manifest at or above {start} (untaped.yml)"
            )
        return self._registry.get(target)

    # internal -----------------------------------------------------------

    def _resolve_from(self, start: Path, *, error: str) -> Workspace:
        start = start.expanduser().resolve()
        for parent in [start, *start.parents]:
            if self._manifests.exists(parent):
                return self._workspace_for(parent)
        raise ConfigError(error)

    def _workspace_for(self, canonical: Path) -> Workspace:
        existing = self._registry.find_by_path(canonical)
        if existing is not None:
            return existing
        # Unregistered manifest — manifest name is the documented source of
        # truth (see AGENTS.md, "Manifest + registry split"); without this
        # precedence, `[<dirname>]` would silently shadow a hand-set `name:`
        # in `status`/`foreach` output.
        manifest = self._manifests.read(canonical)
        return Workspace(name=manifest.name or canonical.name, path=canonical)
