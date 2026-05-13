"""Use case: spawn the user's editor on a workspace directory."""

from __future__ import annotations

from collections.abc import Sequence

from untaped_workspace.application.ports import EditorRunner, RegistryReader
from untaped_workspace.errors import WorkspaceError


class EditWorkspace:
    def __init__(
        self,
        registry: RegistryReader,
        *,
        runner: EditorRunner,
    ) -> None:
        self._registry = registry
        self._runner = runner

    def __call__(self, name: str, *, argv: Sequence[str]) -> int:
        """Append the workspace path to ``argv`` and dispatch to the runner.

        Editor selection (``--editor`` / ``$VISUAL`` / ``$EDITOR`` /
        ``"vi"``), ``shlex`` splitting, and platform branching all live
        in :func:`untaped_workspace.infrastructure.system_adapters.resolve_editor_argv`
        — wired by the CLI composition root so this use case stays pure.
        """
        path = self._registry.get(name).path
        try:
            return self._runner([*argv, str(path)])
        except FileNotFoundError as exc:
            raise WorkspaceError(f"editor not found: {argv[0]}") from exc
