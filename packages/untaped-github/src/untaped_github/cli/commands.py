"""Typer commands for the GitHub domain."""

from __future__ import annotations

import typer
from untaped_core import ColumnsOption, FormatOption, format_output, report_errors

from untaped_github.cli._client import open_client
from untaped_github.cli.search_commands import app as search_app

app = typer.Typer(
    name="github",
    help="Inspect and search GitHub from the authenticated user's account.",
    no_args_is_help=True,
)


@app.callback()
def _callback() -> None:
    """Inspect and search GitHub from the authenticated user's account."""


@app.command("whoami")
def whoami_command(
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Show the authenticated GitHub user (``GET /user``)."""
    from untaped_github.application import WhoAmI  # noqa: PLC0415

    with report_errors(), open_client() as client:
        user = WhoAmI(client)()
        typer.echo(format_output([user.model_dump()], fmt=fmt, columns=columns))


app.add_typer(search_app, name="search")
