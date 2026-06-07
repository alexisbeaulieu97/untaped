"""Typer commands: ``untaped config list / get / set / unset``."""

from __future__ import annotations

import sys

import typer

from untaped import (
    ColumnsOption,
    ConfigError,
    FormatOption,
    OutputFormat,
    ProfileOverrideOption,
    UiContext,
    profile_override,
    report_errors,
    resolve_config_path,
    ui_context,
)
from untaped.config.application import (
    GetSetting,
    ListAllProfilesSettings,
    ListSettings,
    SetSetting,
    UnsetSetting,
)
from untaped.config.domain import SettingEntry
from untaped.config.infrastructure import SettingsFileRepository

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
    profile: ProfileOverrideOption = None,
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
        if all_profiles and profile is not None:
            raise typer.BadParameter("Cannot combine --profile with --all-profiles.")
        with profile_override(profile):
            repo = SettingsFileRepository()
            if all_profiles:
                entries = ListAllProfilesSettings(repo)(reveal_secrets=show_secrets)
            else:
                entries = ListSettings(repo)(reveal_secrets=show_secrets)
        rows = [_entry_to_row(e) for e in entries]
        typer.echo(_render_collection(rows, fmt=fmt, columns=columns))


@app.command("get", no_args_is_help=True)
def get_command(
    key: str = typer.Argument(..., help="Dotted setting key, e.g. `http.verify_ssl`."),
    fmt: OutputFormat = typer.Option("raw", "--format", "-f", help="Output format."),
    profile: ProfileOverrideOption = None,
    show_secrets: bool = typer.Option(
        False, "--show-secrets", help="Reveal secret values instead of `***`."
    ),
) -> None:
    """Print one effective scalar setting value."""
    with report_errors():
        if _is_global_ui_key(key) and profile is not None:
            raise ConfigError("ui settings are global; --profile cannot be used")
        with profile_override(profile):
            entry = GetSetting(SettingsFileRepository())(key, reveal_secrets=show_secrets)
        typer.echo(_render_detail(_entry_to_row(entry), fmt=fmt))


@app.command("set", no_args_is_help=True)
def set_command(
    key: str = typer.Argument(..., help="Dotted setting key, e.g. `http.verify_ssl`."),
    value: str | None = typer.Argument(None, help="New value (parsed as a YAML scalar)."),
    target_profile: str | None = typer.Option(
        None,
        "--target-profile",
        help="Target profile to write to (defaults to the active profile).",
    ),
    stdin: bool = typer.Option(False, "--stdin", help="Read the new value from stdin."),
    prompt: bool = typer.Option(
        False, "--prompt", help="Prompt for the new value without echoing input."
    ),
) -> None:
    """Persist ``key = value`` into a profile (validated against the schema)."""
    with report_errors():
        if _is_global_ui_key(key) and target_profile is not None:
            raise ConfigError("ui settings are global; --target-profile cannot be used")
        resolved_value = _resolve_set_value(value, stdin=stdin, prompt=prompt)
        target = SetSetting(SettingsFileRepository())(key, resolved_value, profile=target_profile)
        if _is_global_ui_key(key):
            message = f"set {key} globally (config: {resolve_config_path()})"
        else:
            message = f"set {key} in profile {target} (config: {resolve_config_path()})"
        ui_context(strict=False).message("success", message)


def _resolve_set_value(value: str | None, *, stdin: bool, prompt: bool) -> str:
    choices = (
        ("VALUE", value is not None),
        ("--stdin", stdin),
        ("--prompt", prompt),
    )
    sources = [name for name, selected in choices if selected]
    if not sources:
        raise typer.BadParameter("provide VALUE, --stdin, or --prompt")
    if len(sources) > 1:
        raise typer.BadParameter("provide only one of VALUE, --stdin, or --prompt")
    if stdin:
        return _read_stdin_value()
    if prompt:
        return _prompt_value()
    assert value is not None
    return value


def _read_stdin_value() -> str:
    if sys.stdin.isatty():
        raise ConfigError("no value received on stdin")
    raw = sys.stdin.read()
    value = raw.rstrip("\r\n")
    if "\n" in value or "\r" in value:
        raise ConfigError("--stdin expects exactly one value")
    if not value.strip():
        raise ConfigError("no value received on stdin")
    return value


def _prompt_value() -> str:
    return ui_context(strict=False).secret("Value")


@app.command("unset", no_args_is_help=True)
def unset_command(
    key: str = typer.Argument(..., help="Dotted setting key to remove."),
    target_profile: str | None = typer.Option(
        None,
        "--target-profile",
        help="Target profile to remove from (defaults to the active profile).",
    ),
) -> None:
    """Remove ``key`` from a profile (no-op if it wasn't set)."""
    with report_errors():
        removed, target = UnsetSetting(SettingsFileRepository())(key, profile=target_profile)
        if _is_global_ui_key(key):
            message = f"unset {key} globally" if removed else f"{key} was not set globally"
            ui_context(strict=False).message("success" if removed else "info", message)
        elif removed:
            ui_context(strict=False).message("success", f"unset {key} in profile {target}")
        else:
            ui_context(strict=False).message("info", f"{key} was not set in profile {target}")


def _entry_to_row(entry: SettingEntry) -> dict[str, object]:
    """Flatten ``SettingEntry`` so JSON, table, and raw all see the same shape."""
    return {
        "key": entry.key,
        "value": entry.value,
        "default": entry.default,
        "source": entry.source.label,
        "profile": entry.profile or "",
    }


def _render_collection(
    rows: list[dict[str, object]],
    *,
    fmt: OutputFormat,
    columns: list[str] | None,
) -> str:
    if fmt == "table":
        return ui_context().collection(rows, fmt=fmt, columns=columns)
    return UiContext().collection(rows, fmt=fmt, columns=columns)


def _render_detail(record: dict[str, object], *, fmt: OutputFormat) -> str:
    columns = ["value"] if fmt == "raw" else None
    if fmt == "table":
        return ui_context().detail(record, fmt=fmt, columns=columns)
    return UiContext().detail(record, fmt=fmt, columns=columns)


def _is_global_ui_key(key: str) -> bool:
    return key.startswith("ui.")
