"""Typer commands: ``untaped config list / set / unset``."""

from __future__ import annotations

import typer
from untaped_core import OutputFormat, format_output, report_errors, resolve_config_path

from untaped_config.application import ListSettings, SetSetting, UnsetSetting
from untaped_config.infrastructure import SettingsFileRepository

app = typer.Typer(
    name="config",
    help="Inspect and modify ``~/.untaped/config.yml``.",
    no_args_is_help=True,
)


@app.callback()
def _callback() -> None:
    """Inspect and modify ``~/.untaped/config.yml``."""


@app.command("list")
def list_command(
    fmt: OutputFormat = typer.Option("table", "--format", "-f", help="Output format."),
    columns: list[str] | None = typer.Option(
        None, "--columns", "-c", help="Columns to include (repeatable)."
    ),
    show_secrets: bool = typer.Option(
        False, "--show-secrets", help="Reveal secret values instead of `***`."
    ),
) -> None:
    """List every configurable setting with its current value, default, and source."""
    with report_errors():
        repo = SettingsFileRepository()
        entries = ListSettings(repo)(reveal_secrets=show_secrets)
        rows = [e.model_dump(exclude={"is_secret"}) for e in entries]
        typer.echo(format_output(rows, fmt=fmt, columns=columns))


@app.command("set", no_args_is_help=True)
def set_command(
    key: str = typer.Argument(..., help="Dotted setting key, e.g. `awx.token`."),
    value: str = typer.Argument(..., help="New value (parsed as a YAML scalar)."),
) -> None:
    """Persist ``key = value`` to the config file (validated against the schema)."""
    with report_errors():
        SetSetting(SettingsFileRepository())(key, value)
        typer.echo(f"set {key} (config: {resolve_config_path()})", err=True)


@app.command("unset", no_args_is_help=True)
def unset_command(
    key: str = typer.Argument(..., help="Dotted setting key to remove."),
) -> None:
    """Remove ``key`` from the config file (no-op if it wasn't set)."""
    with report_errors():
        removed = UnsetSetting(SettingsFileRepository())(key)
        msg = f"unset {key}" if removed else f"{key} was not set"
        typer.echo(msg, err=True)
