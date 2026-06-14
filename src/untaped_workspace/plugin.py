"""Untaped plugin manifest for workspace management commands.

This module must stay off the CLI import path: the manifest's
``CliSpec.import_path`` defers loading ``untaped_workspace.cli`` until the
``workspace`` command is actually dispatched, so importing the Cyclopts app
here would defeat that laziness.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from untaped.api import CliSpec, PluginManifest, SkillSpec

from untaped_workspace.settings import WorkspaceSettings, WorkspaceState


class WorkspacePlugin:
    id = "workspace"
    untaped_api_version = 5

    def manifest(self) -> PluginManifest:
        return PluginManifest(
            clis=(
                CliSpec(
                    name="workspace",
                    import_path="untaped_workspace.cli:app",
                    help="Manage local git workspaces (collections of repos).",
                ),
            ),
            profile_settings={"workspace": WorkspaceSettings},
            state_settings={"workspace": WorkspaceState},
            skills=(
                SkillSpec(
                    name="untaped-workspace",
                    source=Path(
                        str(files("untaped_workspace").joinpath("skills", "untaped-workspace"))
                    ),
                    description="Use the untaped workspace plugin.",
                ),
            ),
        )


plugin = WorkspacePlugin()
