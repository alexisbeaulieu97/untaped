"""Console-script entrypoint for the ``untaped-workspace`` CLI.

``untaped-workspace`` is a standalone tool built on the untaped SDK. ``main()``
hands the workspace cyclopts app and a :class:`ToolSpec` (declaring both the
profile settings and the disjoint tool-managed state) to ``run_tool``, which
mounts the shared ``config`` / ``profile`` / ``skills`` groups and runs under
the SDK's error contract.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from untaped.api import SkillAsset, ToolSpec, run_tool

from untaped_workspace.cli import app
from untaped_workspace.settings import WorkspaceSettings, WorkspaceState

SPEC = ToolSpec(
    command="untaped-workspace",
    section="workspace",
    profile_model=WorkspaceSettings,
    state_model=WorkspaceState,
    skills=(
        SkillAsset(
            name="untaped-workspace",
            source=Path(str(files("untaped_workspace").joinpath("skills", "untaped-workspace"))),
            description="Use the untaped-workspace CLI.",
        ),
    ),
)


def main() -> object:
    """Run the ``untaped-workspace`` CLI."""
    return run_tool(app, SPEC)


if __name__ == "__main__":
    main()
