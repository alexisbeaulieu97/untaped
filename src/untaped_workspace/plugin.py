"""Untaped plugin registration for workspace management commands."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from untaped.plugins import PluginRegistry, SkillSpec

from untaped_workspace import app
from untaped_workspace.settings import WorkspaceSettings, WorkspaceState


class WorkspacePlugin:
    id = "workspace"
    untaped_api_version = 2

    def register(self, registry: PluginRegistry) -> None:
        registry.add_profile_settings("workspace", WorkspaceSettings)
        registry.add_state_settings("workspace", WorkspaceState)
        registry.add_cli("workspace", app)
        registry.add_skill(
            SkillSpec(
                name="untaped-workspace",
                source=Path(
                    str(files("untaped_workspace").joinpath("skills", "untaped-workspace"))
                ),
                description="Use the untaped workspace plugin.",
            )
        )


plugin = WorkspacePlugin()
