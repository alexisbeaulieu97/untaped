"""Cyclopts commands for the GitHub domain."""

from __future__ import annotations

from untaped.api import (
    ColumnsOption,
    FormatOption,
    create_app,
    emit,
    report_errors,
)
from untaped.capabilities.github.cli._client import open_client
from untaped.capabilities.github.cli.cache_commands import app as cache_app
from untaped.capabilities.github.cli.repos_commands import app as repos_app
from untaped.capabilities.github.cli.search_commands import app as search_app
from untaped.capabilities.github.cli.sweep_commands import sweep_command

app = create_app(
    name="github",
    help="Inspect and search GitHub from the authenticated user's account.",
)


@app.command(name="whoami")
def whoami_command(
    *,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Show the authenticated GitHub user (``GET /user``)."""
    from untaped.capabilities.github.application import WhoAmI  # noqa: PLC0415

    with report_errors(), open_client() as (client, ui):
        with ui.progress("Fetching authenticated user…"):
            user = WhoAmI(client)()
        emit(user, fmt=fmt, columns=columns, kind="github.user")


app.command(repos_app, name="repos")
app.command(cache_app, name="cache")
app.command(search_app, name="search")
app.command(sweep_command, name="sweep")
