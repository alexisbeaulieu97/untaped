"""Typer commands for the GitHub domain."""

from __future__ import annotations

import typer
from untaped_core import OutputFormat, format_output

from untaped_github.application import WhoAmI
from untaped_github.infrastructure import GithubClient

app = typer.Typer(
    name="github",
    help="Search and inspect GitHub from the command line.",
    no_args_is_help=True,
)


@app.callback()
def _callback() -> None:
    """Search and inspect GitHub from the command line."""


@app.command("whoami")
def whoami_command(
    fmt: OutputFormat = typer.Option("table", "--format", "-f", help="Output format."),
    columns: list[str] | None = typer.Option(
        None, "--columns", "-c", help="Columns to include (repeatable)."
    ),
) -> None:
    """Show the authenticated GitHub user (``GET /user``)."""
    with GithubClient() as client:
        user = WhoAmI(client)()
    typer.echo(format_output([user.model_dump()], fmt=fmt, columns=columns))
