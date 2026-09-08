"""Workspace capability for the unified ``untaped`` shell."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.capabilities.workspace.settings import WorkspaceSettings, WorkspaceState
from untaped.capability_api import CapabilitySpec, SkillAsset

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
    config_section="workspace",
    profile_model=WorkspaceSettings,
    state_model=WorkspaceState,
    skills=(
        SkillAsset(
            name="untaped-workspace",
            source=Path(
                str(files("untaped.capabilities.workspace").joinpath("skills", "untaped-workspace"))
            ),
            description="Use the built-in `untaped workspace` capability for local git workspaces.",
        ),
    ),
    doctor_checks=(),
)
