"""Use case: list registered workspaces."""

from __future__ import annotations

from untaped_workspace.application.ports import RegistryReader
from untaped_workspace.domain import Workspace


class ListWorkspaces:
    """Return all registered workspaces."""

    def __init__(self, repo: RegistryReader) -> None:
        self._repo = repo

    def __call__(self) -> list[Workspace]:
        return self._repo.entries()
