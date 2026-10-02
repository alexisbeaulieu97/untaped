"""Locate the workspace a command acts on: by name, or from the current directory."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from untaped.capabilities.workspace.errors import WorkspaceNotFoundError
from untaped.sdk import not_found

if TYPE_CHECKING:
    from untaped.capabilities.workspace.application.ports import WorkspaceStore
    from untaped.capabilities.workspace.domain.models import WorkspaceRecord


def locate_workspace(
    store: WorkspaceStore, *, name: str | None, workspaces_dir: Path, cwd: Path
) -> WorkspaceRecord:
    """The active workspace called ``name``, or the one whose directory contains ``cwd``."""
    if name is not None:
        return active_workspace(store, name)
    root = workspaces_dir.expanduser().resolve()
    try:
        parts = cwd.resolve().relative_to(root).parts
    except ValueError:
        parts = ()
    record = store.get(parts[0]) if parts else None
    if record is None:
        raise WorkspaceNotFoundError(
            "not inside a workspace",
            category="usage",
            hint=f"pass a workspace name, or cd into {root}/NAME",
        )
    return record


def workspace_root(workspaces_dir: Path, name: str) -> Path:
    """Absolute directory of workspace ``name`` (``~`` expanded, symlinks resolved)."""
    return workspaces_dir.expanduser().resolve() / name


def active_workspace(store: WorkspaceStore, name: str) -> WorkspaceRecord:
    """The stored active workspace ``name``; not found (listing the known ones) otherwise."""
    record = store.get(name)
    if record is None:
        raise WorkspaceNotFoundError(
            not_found("workspace", name, known=[r.name for r in store.active()])
        )
    return record
