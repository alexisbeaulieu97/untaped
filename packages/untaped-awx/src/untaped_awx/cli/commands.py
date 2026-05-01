"""Typer commands for the AWX / AAP domain."""

from __future__ import annotations

import typer
from untaped_core import OutputFormat, format_output

from untaped_awx.application import Ping
from untaped_awx.infrastructure import AwxClient

app = typer.Typer(
    name="awx",
    help="Talk to Ansible Automation Platform / AWX.",
    no_args_is_help=True,
)


@app.callback()
def _callback() -> None:
    """Talk to Ansible Automation Platform / AWX."""


@app.command("ping")
def ping_command(
    fmt: OutputFormat = typer.Option("table", "--format", "-f", help="Output format."),
    columns: list[str] | None = typer.Option(
        None, "--columns", "-c", help="Columns to include (repeatable)."
    ),
) -> None:
    """Check AAP control-plane health (``/api/v2/ping/``)."""
    with AwxClient() as client:
        status = Ping(client)()
    typer.echo(format_output([status.model_dump()], fmt=fmt, columns=columns))
