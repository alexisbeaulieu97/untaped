"""Typer app composition root for the workspace command group."""

from __future__ import annotations

import typer

from untaped_workspace.cli.branch_commands import app as branch_app
from untaped_workspace.cli.lifecycle_commands import (
    register_import_command,
    register_lifecycle_commands,
)
from untaped_workspace.cli.ops_commands import register_operation_commands
from untaped_workspace.cli.repo_commands import register_repo_commands
from untaped_workspace.cli.ux_commands import register_display_commands, register_ux_commands

app = typer.Typer(
    name="workspace",
    help="Manage local git workspaces (collections of repos).",
    no_args_is_help=True,
)


@app.callback()
def _callback() -> None:
    """Manage local git workspaces."""


app.add_typer(branch_app, name="branch")
register_display_commands(app)
register_lifecycle_commands(app)
register_repo_commands(app)
register_operation_commands(app)
register_import_command(app)
register_ux_commands(app)
