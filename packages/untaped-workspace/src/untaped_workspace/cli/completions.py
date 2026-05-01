"""Typer autocompletion callbacks for workspace names."""

from __future__ import annotations

from collections.abc import Iterable

from untaped_workspace.errors import WorkspaceError
from untaped_workspace.infrastructure import WorkspaceRegistryRepository


def complete_workspace_name(incomplete: str) -> Iterable[str]:
    try:
        names = [w.name for w in WorkspaceRegistryRepository().entries()]
    except WorkspaceError:
        return []
    return [n for n in names if n.startswith(incomplete)]
