"""Use case: list registered workspaces."""

from __future__ import annotations

from untaped.capabilities.workspace.application.ports import RegistryReader
from untaped.capabilities.workspace.domain import Workspace


class ListWorkspaces:
    """Return all registered workspaces."""

    def __init__(self, repo: RegistryReader) -> None:
        self._repo = repo

    def __call__(self) -> list[Workspace]:
        return self._repo.entries()
