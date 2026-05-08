"""Use case: return the absolute path of a registered workspace by name."""

from __future__ import annotations

from pathlib import Path

from untaped_workspace.application.ports import RegistryReader


class WorkspacePath:
    def __init__(self, registry: RegistryReader) -> None:
        self._registry = registry

    def __call__(self, name: str) -> Path:
        return self._registry.get(name).path
