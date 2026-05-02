"""Typer commands: ``untaped config list / set / unset``."""

from __future__ import annotations

import typer
from untaped_core import (
    ColumnsOption,
    FormatOption,
    format_output,
    report_errors,
    resolve_config_path,
)

from untaped_config.application import (
    ListAllProfilesSettings,
    ListSettings,
    SetSetting,
    UnsetSetting,
)
from untaped_config.domain import SettingEntry
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
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
    show_secrets: bool = typer.Option(
        False, "--show-secrets", help="Reveal secret values instead of `***`."
    ),
    all_profiles: bool = typer.Option(
        False,
        "--all-profiles",
        help="Show one row per (profile, key) instead of the resolved view.",
    ),
) -> None:
    """List configurable settings.

    Default view: the effective values resolved from the active profile (with
    fallback to ``default`` and schema defaults). Use ``--all-profiles`` to
    inspect what every profile has set, regardless of which is active.
    """
    with report_errors():
        repo = SettingsFileRepository()
        if all_profiles:
            entries = ListAllProfilesSettings(repo)(reveal_secrets=show_secrets)
        else:
            entries = ListSettings(repo)(reveal_secrets=show_secrets)
        rows = [_entry_to_row(e) for e in entries]
        typer.echo(format_output(rows, fmt=fmt, columns=columns))


@app.command("set", no_args_is_help=True)
def set_command(
    key: str = typer.Argument(..., help="Dotted setting key, e.g. `awx.token`."),
    value: str = typer.Argument(..., help="New value (parsed as a YAML scalar)."),
    profile: str | None = typer.Option(
        None,
        "--profile",
        help="Target profile to write to (defaults to the active profile).",
    ),
) -> None:
    """Persist ``key = value`` into a profile (validated against the schema)."""
    with report_errors():
        target = SetSetting(SettingsFileRepository())(key, value, profile=profile)
        typer.echo(f"set {key} in profile {target} (config: {resolve_config_path()})", err=True)


@app.command("unset", no_args_is_help=True)
def unset_command(
    key: str = typer.Argument(..., help="Dotted setting key to remove."),
    profile: str | None = typer.Option(
        None,
        "--profile",
        help="Target profile to remove from (defaults to the active profile).",
    ),
) -> None:
    """Remove ``key`` from a profile (no-op if it wasn't set)."""
    with report_errors():
        removed, target = UnsetSetting(SettingsFileRepository())(key, profile=profile)
        msg = f"unset {key} in profile {target}" if removed else f"{key} was not set"
        typer.echo(msg, err=True)


def _entry_to_row(entry: SettingEntry) -> dict[str, object]:
    """Flatten ``SettingEntry`` so JSON, table, and raw all see the same shape."""
    return {
        "key": entry.key,
        "value": entry.value,
        "default": entry.default,
        "source": entry.source.label,
        "profile": entry.profile or "",
    }
