"""Untaped plugin registration for workspace management commands."""

from __future__ import annotations

from untaped.plugins import PluginRegistry
from untaped_workspace import app
from untaped_workspace.settings import WorkspaceSettings, WorkspaceState


class WorkspacePlugin:
    id = "workspace"

    def register(self, registry: PluginRegistry) -> None:
        registry.add_profile_settings("workspace", WorkspaceSettings)
        registry.add_state_settings("workspace", WorkspaceState)
        registry.add_cli("workspace", app)


plugin = WorkspacePlugin()
