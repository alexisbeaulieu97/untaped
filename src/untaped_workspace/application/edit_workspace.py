"""Use case: spawn the user's editor on a workspace directory."""

from __future__ import annotations

import os
import shlex

from untaped_workspace.application.ports import EditorRunner, RegistryReader
from untaped_workspace.errors import WorkspaceError


class EditWorkspace:
    def __init__(
        self,
        registry: RegistryReader,
        *,
        runner: EditorRunner,
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
            argv = shlex.split(chosen, posix=os.name != "nt")
        except ValueError as exc:
            raise WorkspaceError(f"could not parse editor command {chosen!r}: {exc}") from exc
        if not argv:
            raise WorkspaceError("editor command is empty")
        try:
            return self._runner([*argv, str(path)])
        except FileNotFoundError as exc:
            raise WorkspaceError(f"editor not found: {argv[0]}") from exc
