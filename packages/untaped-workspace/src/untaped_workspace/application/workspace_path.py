"""Use case: return the absolute path of a registered workspace by name."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class _RegistryReader(Protocol):
    def get(self, name: str) -> _HasPath: ...


class _HasPath(Protocol):
    @property
    def path(self) -> Path: ...


class WorkspacePath:
    def __init__(self, registry: _RegistryReader) -> None:
        self._registry = registry

    def __call__(self, name: str) -> Path:
        return self._registry.get(name).path
