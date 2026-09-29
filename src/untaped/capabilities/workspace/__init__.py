"""Workspace capability for the unified ``untaped`` shell."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.capabilities.workspace.settings import WorkspaceSettings, WorkspaceState
from untaped.capability_api import CapabilitySpec, SkillAsset, executable_check

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
    help="Manage local git workspaces (collections of repos).",
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
                "Use the `untaped workspace` command to manage local multi-repository git "
                "workspaces (register them, add or remove repos, clone and pull them together, "
                "check their status, switch branches, and run a command in every repo). Use "
                "when the user mentions a workspace, several repos at once, cloning or syncing "
                "many repos, dirty or behind repos, or running a command across repos."
            ),
        ),
    ),
    doctor_checks=(executable_check("workspace.git", "git", purpose="workspace commands"),),
)
