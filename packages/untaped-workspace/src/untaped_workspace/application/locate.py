"""Where ``create`` makes a workspace: ``<workspaces_dir>/NAME``."""

from __future__ import annotations

from pathlib import Path

from untaped_workspace.domain.naming import workspace_key


def workspace_root(workspaces_dir: Path, name: str) -> Path:
    """Absolute folder ``create NAME`` makes its project in (``~`` expanded, links resolved)."""
    return workspaces_dir.expanduser().resolve() / workspace_key(name)
