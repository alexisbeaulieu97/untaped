"""Use case: list registered workspaces."""

from __future__ import annotations

from typing import Protocol

from untaped_workspace.domain import Workspace


class WorkspaceRepository(Protocol):
    def entries(self) -> list[Workspace]: ...


class ListWorkspaces:
    """Return all registered workspaces."""

    def __init__(self, repo: WorkspaceRepository) -> None:
        self._repo = repo

    def __call__(self) -> list[Workspace]:
        return self._repo.entries()
