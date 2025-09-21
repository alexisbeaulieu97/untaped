"""GitHub CLI sub-app registration and entrypoint."""

from __future__ import annotations

import typer

from .commands.github_list import app as list_app
from .commands.github_read import app as read_app

app = typer.Typer(
    name="github", help="GitHub repository operations", add_completion=False
)


def register_github_commands(main_app: typer.Typer) -> None:
    """Register GitHub commands with the main CLI app."""
    main_app.add_typer(app, name="github", help="GitHub repository operations")


@app.callback()
def callback() -> None:
    """GitHub repository operations for untaped toolkit."""
    pass


# Register the subcommands
app.add_typer(read_app, name="read-file", help="Read a file from a GitHub repository")
app.add_typer(
    list_app, name="list-directory", help="List files in a GitHub repository directory"
)


# Export the register function for use by the main app
__all__ = ["register_github_commands", "app"]
