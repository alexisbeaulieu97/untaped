"""Factory for the ``<tool> config …`` command group.

``run_tool`` calls :func:`build_config_app` and mounts the result, so each
tool exposes ``config list / get / set / unset`` over its own section of the
shared ``~/.untaped/config.yml``.

Key model:

* A **bare key** addresses the tool's own section: ``untaped-github config
  set token X`` writes ``github.token`` (within the active profile).
* ``ui.theme`` and ``http.*`` are **SDK globals**, written at the top level.
* **State fields** (tool-managed) are not settable here.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
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
    UnsetSetting,
)
from untaped.config.domain import SettingEntry
from untaped.config.infrastructure import GLOBAL_SECTIONS, SettingsFileRepository
from untaped.config_schema import FieldDescriptor
from untaped.errors import ConfigError
from untaped.output import OutputFormat
from untaped.prompts import PromptChoice
from untaped.settings import resolve_config_path
from untaped.theme import BUILTIN_THEMES
from untaped.tool import ToolSpec
from untaped.ui import UiContext, ui_context


@dataclass(frozen=True)
class _Ctx:
    """Per-tool context captured by the config command closures."""

    command: str
    section: str
    section_fields: frozenset[str]
    state_fields: frozenset[str]

    def repo(self) -> SettingsFileRepository:
        return SettingsFileRepository()


def build_config_app(spec: ToolSpec) -> Any:
    """Return the cyclopts ``config`` command group for ``spec``."""
    state_fields = (
        frozenset(spec.state_model.model_fields) if spec.state_model is not None else frozenset()
    )
    ctx = _Ctx(
        command=spec.command,
        section=spec.section,
        section_fields=frozenset(spec.profile_model.model_fields),
        state_fields=state_fields,
    )
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

    return app


def _resolve_key(ctx: _Ctx, key: str) -> str:
    """Map a user key to its fully-qualified config key.

    Bare keys naming a section field are prefixed with the tool's section;
    global prefixes (``ui.``, ``http.``) and base fields pass through. Keys
    naming a tool-managed state field are rejected.
    """
    first = key.split(".", 1)[0]
    if first in GLOBAL_SECTIONS:
        return key
    if first in ctx.state_fields:
        raise ConfigError(f"{key!r} is managed by {ctx.command} and is not a configurable setting")
    if first in ctx.section_fields:
        return f"{ctx.section}.{key}"
    return key


def _is_global(full_key: str) -> bool:
    return full_key.split(".", 1)[0] in GLOBAL_SECTIONS


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
        full = _resolve_key(ctx, key)
        entry = GetSetting(ctx.repo())(full, reveal_secrets=show_secrets)
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
        full = _resolve_key(ctx, key)
        if _is_global(full) and target_profile is not None:
            section = full.split(".", 1)[0]
            raise ConfigError(f"{section} settings are global; --target-profile cannot be used")
        repo = ctx.repo()
        resolved_value = _resolve_set_value(
            full, value, stdin=stdin, prompt=prompt, repo=repo, target_profile=target_profile
        )
        target = SetSetting(repo)(full, resolved_value, profile=target_profile)
        ui_context(strict=False).message("success", _set_message(full, target))


def _set_message(full: str, target: str) -> str:
    path = resolve_config_path()
    if _is_global(full):
        return f"set {full} globally (config: {path})"
    return f"set {full} in profile {target} (config: {path})"


def _unset(ctx: _Ctx, key: str, *, target_profile: str | None) -> None:
    with report_errors():
        full = _resolve_key(ctx, key)
        removed, target = UnsetSetting(ctx.repo())(full, profile=target_profile)
        ui = ui_context(strict=False)
        if _is_global(full):
            msg = f"unset {full} globally" if removed else f"{full} was not set globally"
            ui.message("success" if removed else "info", msg)
            return
        where = f"in profile {target}"
        if removed:
            ui.message("success", f"unset {full} {where}")
        else:
            ui.message("info", f"{full} was not set {where}")


def _resolve_set_value(
    full_key: str,
    value: str | None,
    *,
    stdin: bool,
    prompt: bool,
    repo: SettingsFileRepository,
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


def _prompt_value(
    full_key: str, repo: SettingsFileRepository, *, target_profile: str | None
) -> str:
    descriptor = _descriptor_for_key(full_key, repo)
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


def _descriptor_for_key(full_key: str, repo: SettingsFileRepository) -> FieldDescriptor:
    section = repo.global_section_of(full_key)
    if section is not None:
        return repo.global_descriptor(full_key, section)
    return repo.descriptor(full_key)


def _prompt_default(
    full_key: str,
    descriptor: FieldDescriptor,
    repo: SettingsFileRepository,
    *,
    target_profile: str | None,
) -> str | None:
    from untaped.config.domain import display_default, display_value  # noqa: PLC0415

    entry = GetSetting(repo)(full_key)
    value = str(entry.value)
    if target_profile is not None and entry.source.kind != "env":
        scoped = repo.scope_value_for(descriptor, target_profile)
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
