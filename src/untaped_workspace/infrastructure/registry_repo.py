"""Read/write workspace entries in ``~/.untaped/config.yml``.

The registry is the tool-managed ``workspace`` *state* section: a small
``name → path`` map under the top-level ``workspace.workspaces`` key. Repo
lists live in the per-workspace manifest, not here.

Writes go through the SDK's safe state surface (``mutate_tool_state`` /
``read_tool_state``) rather than reaching into config-file internals: the
shared config file is co-owned by every untaped tool, so a write must only
touch this tool's section and never clobber another's.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from untaped.config_file import mutate_tool_state, read_tool_state

from untaped_workspace.domain import Workspace
from untaped_workspace.errors import RegistryError

_SECTION = "workspace"
_KEY = "workspaces"


class WorkspaceRegistryRepository:
    """Adapter for the centralised ``name → path`` registry."""

    def entries(self) -> list[Workspace]:
        return [_to_workspace(e) for e in _existing(read_tool_state(_SECTION))]

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

        def _apply(state: dict[str, Any]) -> None:
            existing = _existing(state)
            for entry in existing:
                if entry.get("name") == name:
                    raise RegistryError(
                        f"workspace name already registered: {name!r} → {entry.get('path')}"
                    )
                if _canonical(entry.get("path", "")) == canonical:
                    raise RegistryError(
                        f"workspace path already registered: {entry.get('path')} "
                        f"(as {entry.get('name')!r})"
                    )
            state[_KEY] = [*existing, {"name": name, "path": str(path)}]

        mutate_tool_state(_SECTION, _apply)
        return Workspace(name=name, path=canonical)

    def unregister(self, name: str) -> bool:
        removed = False

        def _apply(state: dict[str, Any]) -> None:
            nonlocal removed
            existing = _existing(state)
            new_entries = [e for e in existing if e.get("name") != name]
            if len(new_entries) == len(existing):
                return
            removed = True
            if new_entries:
                state[_KEY] = new_entries
            else:
                # Drop the key when empty so the section is removed entirely.
                state.pop(_KEY, None)

        mutate_tool_state(_SECTION, _apply)
        return removed


def _existing(state: dict[str, Any]) -> list[dict[str, Any]]:
    raw = state.get(_KEY)
    if not isinstance(raw, list):
        return []
    return [e for e in raw if isinstance(e, dict)]


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
