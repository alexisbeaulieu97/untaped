"""Read/write workspace entries in ``~/.untaped/config.yml``.

The registry is a small ``name → path`` map. Repo lists live in the
per-workspace manifest, not here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from untaped_core.config_file import (
    get_at_path,
    read_config_dict,
    set_at_path,
    unset_at_path,
    write_config_dict,
)
from untaped_core.settings import get_settings

from untaped_workspace.domain import Workspace
from untaped_workspace.errors import RegistryError

_REGISTRY_PATH: tuple[str, ...] = ("workspace", "workspaces")


class WorkspaceRegistryRepository:
    """Adapter for the centralised ``name → path`` registry."""

    def entries(self) -> list[Workspace]:
        return [_to_workspace(e) for e in _existing_entries(read_config_dict())]

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
        data = read_config_dict()
        existing = _existing_entries(data)
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
        set_at_path(data, _REGISTRY_PATH, [*existing, {"name": name, "path": str(path)}])
        write_config_dict(data)
        get_settings.cache_clear()
        return Workspace(name=name, path=canonical)

    def unregister(self, name: str) -> bool:
        data = read_config_dict()
        existing = _existing_entries(data)
        new_entries = [e for e in existing if e.get("name") != name]
        if len(new_entries) == len(existing):
            return False
        if new_entries:
            set_at_path(data, _REGISTRY_PATH, new_entries)
        else:
            unset_at_path(data, _REGISTRY_PATH)
        write_config_dict(data)
        get_settings.cache_clear()
        return True


def _existing_entries(data: dict[str, Any]) -> list[dict[str, Any]]:
    raw = get_at_path(data, _REGISTRY_PATH)
    if not isinstance(raw, list):
        return []
    return [e for e in raw if isinstance(e, dict)]


def _to_workspace(entry: dict[str, Any]) -> Workspace:
    return Workspace(name=str(entry["name"]), path=_canonical(entry["path"]))


def _canonical(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()
