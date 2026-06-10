"""Cyclopts commands: ``untaped config list / get / set / unset``."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from typing import Annotated, Literal, get_args, get_origin

from cyclopts import Parameter

from untaped import (
    BUILTIN_THEMES,
    ColumnsOption,
    ConfigError,
    FieldDescriptor,
    FormatOption,
    OutputFormat,
    ProfileOverrideOption,
    PromptChoice,
    UiContext,
    create_app,
    echo,
    profile_override,
    raise_usage,
    render_rows,
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
from untaped.plugin_registry import current_registry

app = create_app(
    name="config",
    help="Inspect and modify ``~/.untaped/config.yml``.",
)


@app.command(name="list")
def list_command(
    *,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
    profile: ProfileOverrideOption = None,
    show_secrets: Annotated[
        bool,
        Parameter(name="--show-secrets", help="Reveal secret values instead of `***`."),
    ] = False,
    all_profiles: Annotated[
        bool,
        Parameter(
            name="--all-profiles",
            help="Show one row per (profile, key) instead of the resolved view.",
        ),
    ] = False,
) -> None:
    """List configurable settings.

    Default view: the effective values resolved from the active profile (with
    fallback to ``default`` and schema defaults). Use ``--all-profiles`` to
    inspect what every profile has set, regardless of which is active.
    """
    with report_errors():
        if all_profiles and profile is not None:
            raise_usage("Cannot combine --profile with --all-profiles.")
        with profile_override(profile):
            repo = SettingsFileRepository()
            if all_profiles:
                entries = ListAllProfilesSettings(repo)(reveal_secrets=show_secrets)
            else:
                entries = ListSettings(repo)(reveal_secrets=show_secrets)
        rows = [_entry_to_row(e) for e in entries]
        echo(render_rows(rows, fmt=fmt, columns=columns))


@app.command(name="get")
def get_command(
    key: Annotated[
        str,
        Parameter(help="Dotted setting key, e.g. `http.verify_ssl`."),
    ],
    /,
    *,
    fmt: FormatOption = "raw",
    profile: ProfileOverrideOption = None,
    show_secrets: Annotated[
        bool,
        Parameter(name="--show-secrets", help="Reveal secret values instead of `***`."),
    ] = False,
) -> None:
    """Print one effective scalar setting value."""
    with report_errors():
        if _is_global_ui_key(key) and profile is not None:
            raise ConfigError("ui settings are global; --profile cannot be used")
        with profile_override(profile):
            entry = GetSetting(SettingsFileRepository())(key, reveal_secrets=show_secrets)
        echo(_render_detail(_entry_to_row(entry), fmt=fmt))


@app.command(name="set")
def set_command(
    key: Annotated[
        str,
        Parameter(help="Dotted setting key, e.g. `http.verify_ssl`."),
    ],
    value: Annotated[
        str | None,
        Parameter(help="New value (parsed as a YAML scalar)."),
    ] = None,
    /,
    *,
    target_profile: Annotated[
        str | None,
        Parameter(
            name="--target-profile",
            help="Target profile to write to (defaults to the active profile).",
        ),
    ] = None,
    stdin: Annotated[
        bool,
        Parameter(name="--stdin", help="Read the new value from stdin."),
    ] = False,
    prompt: Annotated[
        bool,
        Parameter(name="--prompt", help="Prompt for the new value using the setting type."),
    ] = False,
) -> None:
    """Persist ``key = value`` into a profile (validated against the schema)."""
    with report_errors():
        if _is_global_ui_key(key) and target_profile is not None:
            raise ConfigError("ui settings are global; --target-profile cannot be used")
        repo = SettingsFileRepository()
        resolved_value = _resolve_set_value(
            key,
            value,
            stdin=stdin,
            prompt=prompt,
            repo=repo,
            target_profile=target_profile,
        )
        target = SetSetting(repo)(key, resolved_value, profile=target_profile)
        if _is_global_ui_key(key):
            message = f"set {key} globally (config: {resolve_config_path()})"
        else:
            message = f"set {key} in profile {target} (config: {resolve_config_path()})"
        ui_context(strict=False).message("success", message)


def _resolve_set_value(
    key: str,
    value: str | None,
    *,
    stdin: bool,
    prompt: bool,
    repo: SettingsFileRepository,
    target_profile: str | None,
) -> str:
    choices = (
        ("VALUE", value is not None),
        ("--stdin", stdin),
        ("--prompt", prompt),
    )
    sources = [name for name, selected in choices if selected]
    if not sources:
        raise_usage("provide VALUE, --stdin, or --prompt")
    if len(sources) > 1:
        raise_usage("provide only one of VALUE, --stdin, or --prompt")
    if stdin:
        return _read_stdin_value()
    if prompt:
        return _prompt_value(key, repo, target_profile=target_profile)
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


def _prompt_value(
    key: str,
    repo: SettingsFileRepository,
    *,
    target_profile: str | None,
) -> str:
    descriptor = _descriptor_for_key(key, repo)
    message = f"Value for {key}"
    ui = ui_context(strict=False)
    if descriptor.is_secret:
        return ui.secret(message)
    default = _prompt_default(key, descriptor, repo, target_profile=target_profile)
    if key == "ui.theme":
        selected_theme = ui.select(message, _theme_choices(), default=default, search=True)
        return _raw_prompt_scalar(selected_theme)
    literal_values = _literal_values(descriptor)
    if literal_values:
        selected_literal = ui.select(
            message,
            [PromptChoice(value=item, label=str(item)) for item in literal_values],
            default=_default_choice(default, literal_values),
        )
        return _raw_prompt_scalar(selected_literal)
    if descriptor.annotation is bool:
        bool_values = ["true", "false"]
        return ui.select(
            message,
            [PromptChoice(value=item, label=item) for item in bool_values],
            default=default if default in bool_values else None,
        )
    return ui.text(message, default=default)


def _descriptor_for_key(key: str, repo: SettingsFileRepository) -> FieldDescriptor:
    if _is_global_ui_key(key):
        return repo.ui_descriptor(key)
    return repo.descriptor(key)


def _prompt_default(
    key: str,
    descriptor: FieldDescriptor,
    repo: SettingsFileRepository,
    *,
    target_profile: str | None,
) -> str | None:
    with profile_override(target_profile):
        entry = GetSetting(repo)(key)
    if entry.value in {"", "—", "***"}:
        return None
    value = str(entry.value)
    if descriptor.annotation is bool:
        return value.lower()
    return value


def _literal_values(descriptor: FieldDescriptor) -> list[object]:
    if get_origin(descriptor.annotation) is Literal:
        return list(get_args(descriptor.annotation))
    return []


def _theme_choices() -> list[PromptChoice[str]]:
    names = sorted({*BUILTIN_THEMES, *current_registry().themes})
    return [PromptChoice(value=name, label=name) for name in names]


def _default_choice(default: str | None, choices: Sequence[object]) -> object | None:
    if default is None:
        return None
    return next((choice for choice in choices if str(choice) == default), None)


def _raw_prompt_scalar(value: object) -> str:
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    return str(value)


@app.command(name="unset")
def unset_command(
    key: Annotated[str, Parameter(help="Dotted setting key to remove.")],
    /,
    *,
    target_profile: Annotated[
        str | None,
        Parameter(
            name="--target-profile",
            help="Target profile to remove from (defaults to the active profile).",
        ),
    ] = None,
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


def _render_detail(record: dict[str, object], *, fmt: OutputFormat) -> str:
    columns = ["value"] if fmt == "raw" else None
    if fmt == "table":
        return ui_context().detail(record, fmt=fmt, columns=columns)
    return UiContext().detail(record, fmt=fmt, columns=columns)


def _is_global_ui_key(key: str) -> bool:
    return key.startswith("ui.")
