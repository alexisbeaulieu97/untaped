"""Typer commands: ``untaped profile list / show / use / create / delete / rename``."""

from __future__ import annotations

import json
from typing import Literal

import typer
import yaml
from untaped_core import (
    ColumnsOption,
    FormatOption,
    Settings,
    format_output,
    redact_secrets,
    report_errors,
    resolve_config_path,
    secret_field_paths,
)

from untaped_profile.application import (
    CreateProfile,
    CurrentProfile,
    DeleteProfile,
    ListProfiles,
    RenameProfile,
    ShowProfile,
    UseProfile,
)
from untaped_profile.infrastructure import ProfileFileRepository

# `profile show` returns a single nested object — `raw`/`table` (which want
# tabular rows) don't apply, so narrow the format type for this command and
# let Typer reject other values at parse time.
ShowFormat = Literal["yaml", "json"]

app = typer.Typer(
    name="profile",
    help="Manage configuration profiles in ``~/.untaped/config.yml``.",
    no_args_is_help=True,
)


@app.callback()
def _callback() -> None:
    """Manage configuration profiles in ``~/.untaped/config.yml``."""


@app.command("list")
def list_command(
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """List every profile, marking which one is active."""
    with report_errors():
        profiles = ListProfiles(ProfileFileRepository())()
        rows: list[dict[str, object]] = [
            {
                "name": p.name,
                "active": "✓" if p.is_active else "",
                "keys": p.key_count,
            }
            for p in profiles
        ]
        typer.echo(format_output(rows, fmt=fmt, columns=columns))


@app.command("show", no_args_is_help=True)
def show_command(
    name: str = typer.Argument(..., help="Profile name to inspect."),
    raw: bool = typer.Option(
        False,
        "--raw",
        help="Show only the keys this profile literally sets (no `default` fallback merge).",
    ),
    show_secrets: bool = typer.Option(
        False,
        "--show-secrets",
        help="Reveal secret values instead of `***`.",
    ),
    fmt: ShowFormat = typer.Option(
        "yaml",
        "--format",
        "-f",
        help="Output format (yaml or json).",
    ),
) -> None:
    """Print a profile's contents (effective view by default).

    ``yaml`` (default) prints the merged data as a flat YAML document for
    human use; ``json`` emits a wrapped envelope so downstream tools can
    address the metadata fields (``jq '.data.awx.base_url'`` etc.).
    ``raw`` and ``table`` are not supported — a single nested object has
    no rows for those formats to render.

    Secrets (AWX/GitHub tokens) are masked as ``***`` in both formats
    unless ``--show-secrets`` is passed, mirroring ``untaped config list``.
    """
    with report_errors():
        profile = ShowProfile(ProfileFileRepository())(name, raw=raw)
        header = f"# profile: {profile.name}"
        if profile.is_active:
            header += " (active)"
        if not raw:
            header += " — effective view (default ⤥ named)"
        typer.echo(header, err=True)
        data = (
            profile.data
            if show_secrets
            else redact_secrets(profile.data, secret_field_paths(Settings))
        )
        if fmt == "json":
            envelope = {
                "name": profile.name,
                "active": profile.is_active,
                "raw": raw,
                "data": data,
            }
            typer.echo(json.dumps(envelope))
        else:
            typer.echo(yaml.safe_dump(data, sort_keys=False, default_flow_style=False).rstrip())


@app.command("use", no_args_is_help=True)
def use_command(
    name: str = typer.Argument(..., help="Profile to activate."),
) -> None:
    """Persist ``active: <name>`` in the config file."""
    with report_errors():
        UseProfile(ProfileFileRepository())(name)
        typer.echo(f"active profile: {name} (config: {resolve_config_path()})", err=True)


@app.command("current")
def current_command() -> None:
    """Print the effective active profile name to stdout (pipe-friendly).

    Honours ``UNTAPED_PROFILE`` and the root ``--profile`` flag, falling
    back to ``default`` when neither is set. The source of the answer
    (``env`` / ``config`` / ``fallback``) goes to stderr so stdout stays
    a single bare profile name suitable for piping.
    """
    with report_errors():
        result = CurrentProfile(ProfileFileRepository())()
        typer.echo(result.name)
        typer.echo(f"(source: {result.source})", err=True)


@app.command("create", no_args_is_help=True)
def create_command(
    name: str = typer.Argument(..., help="Name of the new profile."),
    copy_from: str | None = typer.Option(
        None,
        "--copy-from",
        help="Existing profile to copy as a starting point.",
    ),
) -> None:
    """Create a new profile (empty by default; use ``--copy-from`` to seed it)."""
    with report_errors():
        CreateProfile(ProfileFileRepository())(name, copy_from=copy_from)
        suffix = f" (copied from {copy_from})" if copy_from else ""
        typer.echo(f"created profile: {name}{suffix}", err=True)


@app.command("delete", no_args_is_help=True)
def delete_command(
    name: str = typer.Argument(..., help="Profile to remove."),
) -> None:
    """Delete a profile. Refuses to delete ``default`` or the active profile."""
    with report_errors():
        DeleteProfile(ProfileFileRepository())(name)
        typer.echo(f"deleted profile: {name}", err=True)


@app.command("rename", no_args_is_help=True)
def rename_command(
    old_name: str = typer.Argument(..., help="Existing profile name."),
    new_name: str = typer.Argument(..., help="New profile name."),
) -> None:
    """Rename a profile, updating ``active:`` if it pointed at the old name."""
    with report_errors():
        RenameProfile(ProfileFileRepository())(old_name, new_name)
        typer.echo(f"renamed profile: {old_name} → {new_name}", err=True)
