"""Typer commands for the GitHub domain."""

from __future__ import annotations

import typer
from untaped_core import ColumnsOption, FormatOption, format_output, report_errors

from untaped_github.application import WhoAmI
from untaped_github.infrastructure import GithubClient

app = typer.Typer(
    name="github",
    help="Inspect the authenticated GitHub user.",
    no_args_is_help=True,
)


@app.callback()
def _callback() -> None:
    """Inspect the authenticated GitHub user."""


@app.command("whoami")
def whoami_command(
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Show the authenticated GitHub user (``GET /user``)."""
    with report_errors():
        with GithubClient() as client:
            user = WhoAmI(client)()
        typer.echo(format_output([user.model_dump()], fmt=fmt, columns=columns))
