"""Read/write workspace entries in ``~/.untaped/config.yml``.

The registry is the tool-managed ``workspace`` *state* section: a small
``name → path`` map under the top-level ``workspace.workspaces`` key. Repo
lists live in the per-workspace manifest, not here.

Writes go through the SDK's ``StateCollection`` helper, which keeps updates
section-scoped, locked, and atomic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from untaped.api import StateCollection

from untaped_workspace.domain import Workspace
from untaped_workspace.errors import RegistryError

_SECTION = "workspace"
_KEY = "workspaces"


def _collection() -> StateCollection:
    return StateCollection(_SECTION, _KEY, id_field="name")


class WorkspaceRegistryRepository:
    """Adapter for the centralised ``name → path`` registry."""

    def entries(self) -> list[Workspace]:
        return [_to_workspace(e) for e in _collection().entries()]

    def get(self, name: str) -> Workspace:
        for ws in self.entries():
            if ws.name == name:
                return ws
        raise RegistryError(f"unknown workspace: {name!r}")

    def find_by_path(self, path: Path) -> Workspace | None:
        target = _canonical(path)
        for ws in self.entries():
            if _canonical(ws.path) == target:
                return ws
        return None

    def register(self, *, name: str, path: Path) -> Workspace:
        canonical = _canonical(path)

        def _register(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
            for entry in entries:
                if entry.get("name") == name:
                    raise RegistryError(
                        f"workspace name already registered: {name!r} → {entry.get('path')}"
                    )
                if _canonical(entry.get("path", "")) == canonical:
                    raise RegistryError(
                        f"workspace path already registered: {entry.get('path')} "
                        f"(as {entry.get('name')!r})"
                    )
            return [*entries, {"name": name, "path": str(path)}]

        _collection().mutate(_register)
        return Workspace(name=name, path=canonical)

    def unregister(self, name: str) -> bool:
        return _collection().remove(name)


def _to_workspace(entry: dict[str, Any]) -> Workspace:
    name = entry.get("name")
    path = entry.get("path")
    if not isinstance(name, str) or not name:
        raise RegistryError(
            f"invalid workspace registry entry: missing or empty 'name' (got {entry!r})"
        )
    if not isinstance(path, str) or not path:
        raise RegistryError(f"invalid workspace registry entry {name!r}: missing or empty 'path'")
    return Workspace(name=name, path=_canonical(path))


def _canonical(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()
