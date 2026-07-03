"""Factory for the ``<tool> config …`` command group.

``run_tool`` calls :func:`build_config_app` and mounts the result, so each
tool exposes ``config list / get / set / unset`` over its own section of the
shared ``~/.untaped/config.yml``.

Key model:

* A **bare key** addresses the tool's own section: ``untaped-github config
  set token X`` writes ``github.token`` (within the active profile).
* ``ui.*``, ``http.*`` and ``log_level`` are SDK-owned per-profile settings,
  addressed by their fully-qualified key and written within the active (or
  ``--target-profile``) profile like any other setting.
* **State fields** (tool-managed) are not settable here.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Literal, get_args, get_origin

from cyclopts import Parameter

from untaped.cli import (
    ColumnsOption,
    FormatOption,
    create_app,
    echo,
    raise_usage,
    render_rows,
    report_errors,
)
from untaped.config.application import (
    GetSetting,
    ListAllProfilesSettings,
    ListSettings,
    SetSetting,
    SettingsReader,
    SettingsRepository,
    ToolConfigContext,
    UnsetSetting,
)
from untaped.config.domain import SettingEntry
from untaped.config.infrastructure import SettingsFileRepository
from untaped.config_file import read_config_dict
from untaped.config_schema import FieldDescriptor
from untaped.errors import ConfigError
from untaped.profile_resolver import classify_active_profile
from untaped.prompts import PromptChoice
from untaped.render import OutputFormat
from untaped.settings import get_settings, legacy_flat_sections, resolve_config_path
from untaped.theme import BUILTIN_THEMES
from untaped.tool import ToolSpec
from untaped.ui import UiContext, ui_context


@dataclass(frozen=True)
class _Ctx:
    """Per-tool context captured by the config command closures."""

    config: ToolConfigContext

    @property
    def command(self) -> str:
        return self.config.command

    def repo(self) -> SettingsRepository:
        return SettingsFileRepository()


def build_config_app(spec: ToolSpec) -> Any:
    """Return the cyclopts ``config`` command group for ``spec``."""
    ctx = _Ctx(config=ToolConfigContext.from_spec(spec))
    app = create_app(name="config", help="Inspect and modify ``~/.untaped/config.yml``.")

    @app.command(name="list")
    def list_command(
        *,
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
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
        """List this tool's settings (resolved effective values by default)."""
        _list(ctx, fmt=fmt, columns=columns, show_secrets=show_secrets, all_profiles=all_profiles)

    @app.command(name="get")
    def get_command(
        key: Annotated[str, Parameter(help="Setting key (bare = this tool's section).")],
        /,
        *,
        fmt: FormatOption = "raw",
        show_secrets: Annotated[
            bool,
            Parameter(name="--show-secrets", help="Reveal secret values instead of `***`."),
        ] = False,
    ) -> None:
        """Print one effective scalar setting value."""
        _get(ctx, key, fmt=fmt, show_secrets=show_secrets)

    @app.command(name="set")
    def set_command(
        key: Annotated[str, Parameter(help="Setting key (bare = this tool's section).")],
        value: Annotated[str | None, Parameter(help="New value (parsed as a YAML scalar).")] = None,
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
            bool, Parameter(name="--stdin", help="Read the value from stdin.")
        ] = False,
        prompt: Annotated[
            bool,
            Parameter(name="--prompt", help="Prompt for the value using the setting type."),
        ] = False,
    ) -> None:
        """Persist ``key = value`` (validated against the schema)."""
        _set(ctx, key, value, target_profile=target_profile, stdin=stdin, prompt=prompt)

    @app.command(name="unset")
    def unset_command(
        key: Annotated[str, Parameter(help="Setting key to remove.")],
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
        """Remove ``key`` from the resolved write scope (no-op if it wasn't set)."""
        _unset(ctx, key, target_profile=target_profile)

    @app.command(name="doctor")
    def doctor_command() -> None:
        """Diagnose the config: active profile, layout issues, and resolved values."""
        _doctor(ctx)

    @app.command(name="edit")
    def edit_command() -> None:
        """Open ``~/.untaped/config.yml`` in $VISUAL/$EDITOR and re-validate on save."""
        _edit()

    return app


def _list(
    ctx: _Ctx,
    *,
    fmt: OutputFormat,
    columns: list[str] | None,
    show_secrets: bool,
    all_profiles: bool,
) -> None:
    with report_errors():
        repo = ctx.repo()
        if all_profiles:
            entries = ListAllProfilesSettings(repo)(reveal_secrets=show_secrets)
        else:
            entries = ListSettings(repo)(reveal_secrets=show_secrets)
        rows = [_entry_to_row(e) for e in entries]
        echo(render_rows(rows, fmt=fmt, columns=columns))


def _get(ctx: _Ctx, key: str, *, fmt: OutputFormat, show_secrets: bool) -> None:
    with report_errors():
        entry = GetSetting(ctx.repo(), context=ctx.config)(key, reveal_secrets=show_secrets)
        echo(_render_detail(_entry_to_row(entry), fmt=fmt))


def _set(
    ctx: _Ctx,
    key: str,
    value: str | None,
    *,
    target_profile: str | None,
    stdin: bool,
    prompt: bool,
) -> None:
    with report_errors():
        repo = ctx.repo()
        full = ctx.config.resolve_key(key)
        resolved_value = _resolve_set_value(
            full, value, stdin=stdin, prompt=prompt, repo=repo, target_profile=target_profile
        )
        result = SetSetting(repo, context=ctx.config)(key, resolved_value, profile=target_profile)
        message = f"set {result.key} in profile {result.profile} (config: {resolve_config_path()})"
        ui_context(strict=False).message("success", message)


def _unset(ctx: _Ctx, key: str, *, target_profile: str | None) -> None:
    with report_errors():
        result = UnsetSetting(ctx.repo(), context=ctx.config)(key, profile=target_profile)
        ui = ui_context(strict=False)
        where = f"in profile {result.profile}"
        if result.removed:
            ui.message("success", f"unset {result.key} {where}")
        else:
            ui.message("info", f"{result.key} was not set {where}")


def _warn_legacy_flat(ui: UiContext, raw: Mapping[str, Any]) -> None:
    """Warn about each top-level section the v2 profiles layout silently ignores."""
    for section in legacy_flat_sections(raw):
        ui.message(
            "warning",
            f"top-level `{section}:` is ignored since the v2 profiles layout — "
            f"move it under `profiles.default.{section}`",
        )


def _doctor(ctx: _Ctx) -> None:
    with report_errors():
        ui = ui_context(strict=False)
        path = resolve_config_path()
        ui.message("info", f"config: {path}")
        raw = read_config_dict(path)
        name, source = classify_active_profile(raw)
        ui.message("info", f"active profile: {name or 'default'} (source: {source})")
        repo = ctx.repo()
        profiles = repo.profile_names()
        if profiles:
            ui.message("info", f"profiles: {', '.join(profiles)}")
        _warn_legacy_flat(ui, raw)
        get_settings.cache_clear()
        get_settings()  # raises ConfigError (→ clean error, exit 1) if invalid
        ui.message("success", "config loaded OK")
        echo(render_rows([_entry_to_row(e) for e in ListSettings(repo)()], fmt="table"))


def _edit() -> None:
    with report_errors():
        editor = shlex.split(os.environ.get("VISUAL") or os.environ.get("EDITOR") or "")
        if not editor:
            raise ConfigError("set $VISUAL or $EDITOR to use `config edit`")
        path = resolve_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run([*editor, str(path)], check=True)
        except FileNotFoundError as exc:
            raise ConfigError(f"editor not found: {editor[0]}") from exc
        except subprocess.CalledProcessError as exc:
            raise ConfigError(f"editor exited with status {exc.returncode}") from exc
        ui = ui_context(strict=False)
        _warn_legacy_flat(ui, read_config_dict(path))
        get_settings.cache_clear()
        get_settings()  # raises ConfigError if the edited file is invalid
        ui.message("success", f"config saved and validated (config: {path})")


def _resolve_set_value(
    full_key: str,
    value: str | None,
    *,
    stdin: bool,
    prompt: bool,
    repo: SettingsReader,
    target_profile: str | None,
) -> str:
    choices = (("VALUE", value is not None), ("--stdin", stdin), ("--prompt", prompt))
    sources = [name for name, selected in choices if selected]
    if not sources:
        raise_usage("provide VALUE, --stdin, or --prompt")
    if len(sources) > 1:
        raise_usage("provide only one of VALUE, --stdin, or --prompt")
    if stdin:
        return _read_stdin_value()
    if prompt:
        return _prompt_value(full_key, repo, target_profile=target_profile)
    assert value is not None
    return value


def _read_stdin_value() -> str:
    if sys.stdin.isatty():
        raise ConfigError("no value received on stdin")
    value = sys.stdin.read().rstrip("\r\n")
    if "\n" in value or "\r" in value:
        raise ConfigError("--stdin expects exactly one value")
    if not value.strip():
        raise ConfigError("no value received on stdin")
    return value


def _prompt_value(full_key: str, repo: SettingsReader, *, target_profile: str | None) -> str:
    descriptor = repo.descriptor(full_key)
    message = f"Value for {full_key}"
    ui = ui_context(strict=False)
    if descriptor.is_secret:
        return ui.secret(message)
    default = _prompt_default(full_key, descriptor, repo, target_profile=target_profile)
    if full_key == "ui.theme":
        return _raw_prompt_scalar(
            ui.select(message, _theme_choices(), default=default, search=True)
        )
    literal_values = _literal_values(descriptor)
    if literal_values:
        return _raw_prompt_scalar(
            ui.select(
                message,
                [PromptChoice(value=item, label=str(item)) for item in literal_values],
                default=_default_choice(default, literal_values),
            )
        )
    if descriptor.annotation is bool:
        bool_values = ["true", "false"]
        return ui.select(
            message,
            [PromptChoice(value=item, label=item) for item in bool_values],
            default=default if default in bool_values else None,
        )
    return ui.text(message, default=default)


def _prompt_default(
    full_key: str,
    descriptor: FieldDescriptor,
    repo: SettingsReader,
    *,
    target_profile: str | None,
) -> str | None:
    from untaped.config.domain import display_default, display_value  # noqa: PLC0415

    entry = GetSetting(repo)(full_key)
    value = str(entry.value)
    if target_profile is not None and entry.source.kind != "env":
        scoped = repo.profile_value_for(descriptor, target_profile)
        value = (
            display_default(descriptor)
            if scoped is None
            else display_value(descriptor, scoped, reveal_secrets=False)
        )
    if value in {"", "—", "***"}:
        return None
    if descriptor.annotation is bool:
        return value.lower()
    return value


def _literal_values(descriptor: FieldDescriptor) -> list[object]:
    if get_origin(descriptor.annotation) is Literal:
        return list(get_args(descriptor.annotation))
    return []


def _theme_choices() -> list[PromptChoice[str]]:
    return [PromptChoice(value=name, label=name) for name in sorted(BUILTIN_THEMES)]


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


def _entry_to_row(entry: SettingEntry) -> dict[str, object]:
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


__all__ = ["build_config_app"]
