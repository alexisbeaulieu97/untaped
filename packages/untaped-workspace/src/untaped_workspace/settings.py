"""Settings model contributed by the workspace plugin."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class WorkspaceEntry(BaseModel):
    """One row mapping a workspace name to its directory."""

    name: str
    path: str


class WorkspaceSettings(BaseModel):
    """User-tunable workspace profile settings."""

    cache_dir: Path = Field(default=Path("~/.untaped/repositories"))
    workspaces_dir: Path = Field(default=Path("~/.untaped/workspaces"))


class WorkspaceState(BaseModel):
    """Top-level workspace app state."""

    workspaces: list[WorkspaceEntry] = Field(default_factory=list)
