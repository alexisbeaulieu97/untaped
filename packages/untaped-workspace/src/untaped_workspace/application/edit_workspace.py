"""Use case: spawn the user's editor on a workspace directory."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from untaped_workspace.errors import WorkspaceError


class _RegistryReader(Protocol):
    def get(self, name: str) -> _HasPath: ...


class _HasPath(Protocol):
    @property
    def path(self) -> Path: ...


Runner = Callable[[Sequence[str]], int]


def _default_runner(cmd: Sequence[str]) -> int:
    completed = subprocess.run(list(cmd), check=False)
    return completed.returncode


class EditWorkspace:
    def __init__(
        self,
        registry: _RegistryReader,
        *,
        runner: Runner = _default_runner,
        env: dict[str, str] | None = None,
    ) -> None:
        self._registry = registry
        self._runner = runner
        self._env = env if env is not None else os.environ

    def __call__(
        self,
        name: str,
        *,
        editor: str | None = None,
    ) -> int:
        path = self._registry.get(name).path
        chosen = editor or self._env.get("VISUAL") or self._env.get("EDITOR") or "vi"
        try:
            return self._runner([chosen, str(path)])
        except FileNotFoundError as exc:
            raise WorkspaceError(f"editor not found: {chosen}") from exc
