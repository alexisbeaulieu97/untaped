"""Typer commands for the GitHub domain."""

from __future__ import annotations

import typer
from untaped_core import ColumnsOption, FormatOption, format_output, get_settings, report_errors

from untaped_github.application import WhoAmI
from untaped_github.cli.search_commands import app as search_app
from untaped_github.infrastructure import GithubClient, GithubConfig

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
    with report_errors():
        settings = get_settings()
        config = GithubConfig(
            base_url=settings.github.base_url,
            token=settings.github.token,
        )
        with GithubClient(config, http=settings.http) as client:
            user = WhoAmI(client)()
        typer.echo(format_output([user.model_dump()], fmt=fmt, columns=columns))


app.add_typer(search_app, name="search")
