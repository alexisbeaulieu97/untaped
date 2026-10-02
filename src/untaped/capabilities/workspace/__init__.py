"""Workspace capability for the unified ``untaped`` shell."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.capabilities.workspace.settings import WorkspaceSettings, WorkspaceState
from untaped.sdk import CapabilitySpec, SkillAsset, executable_check

if TYPE_CHECKING:
    from cyclopts import App

__all__ = ["SPEC", "build_app"]


def build_app() -> App:
    """Nullary factory returning the workspace cyclopts app."""
    from untaped.capabilities.workspace.cli import app  # noqa: PLC0415

    return app


SPEC = CapabilitySpec(
    name="workspace",
    app_factory=build_app,
    help=(
        "Create and archive task workspaces (git worktrees of several repos). "
        "Experimental: may change in a minor release."
    ),
    config_section="workspace",
    profile_model=WorkspaceSettings,
    state_model=WorkspaceState,
    skills=(
        SkillAsset(
            name="untaped-workspace",
            source=Path(
                str(files("untaped.capabilities.workspace").joinpath("skills", "untaped-workspace"))
            ),
            description=(
                "Creates, inspects and archives task workspaces through the `untaped workspace` "
                "command (one directory per task holding git worktrees of several repos on a "
                "shared branch, safe archiving once work is pushed). Use when the user starts "
                "work on a ticket across repos, asks where a workspace is, or wants to clean "
                "one up."
            ),
        ),
    ),
    doctor_checks=(executable_check("workspace.git", "git", purpose="workspace commands"),),
)
