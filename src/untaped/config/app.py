"""Factory for the ``<tool> config ...`` command group."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from cyclopts import App, Parameter

from untaped.cli import (
    ColumnsOption,
    FormatOption,
    create_app,
    emit,
    report_errors,
)
from untaped.config.doctor import run_config_doctor
from untaped.config.editor import run_config_editor
from untaped.config.models import setting_entry_row
from untaped.config.ports import SettingsRepository
from untaped.config.prompting import resolve_set_value
from untaped.config.repository import SettingsFileRepository
from untaped.config.use_cases import (
    GetSetting,
    ListAllProfilesSettings,
    ListSettings,
    SetSetting,
    ToolConfigContext,
    UnsetSetting,
)
from untaped.render import OutputFormat
from untaped.settings import resolve_config_path
from untaped.tool import ToolSpec
from untaped.ui import ui_context


@dataclass(frozen=True)
class _Ctx:
    """Per-tool context captured by the config command closures."""

    config: ToolConfigContext

    @property
    def command(self) -> str:
        return self.config.command

    def repo(self) -> SettingsRepository:
        return SettingsFileRepository()


def build_config_app(spec: ToolSpec) -> App:
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
        run_config_doctor(ctx.repo())

    @app.command(name="edit")
    def edit_command() -> None:
        """Open ``~/.untaped/config.yml`` in $VISUAL/$EDITOR and re-validate on save."""
        run_config_editor()

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
        rows = [setting_entry_row(e) for e in entries]
        emit(rows, fmt=fmt, columns=columns)


def _get(ctx: _Ctx, key: str, *, fmt: OutputFormat, show_secrets: bool) -> None:
    with report_errors():
        entry = GetSetting(ctx.repo(), context=ctx.config)(key, reveal_secrets=show_secrets)
        columns = ["value"] if fmt == "raw" else None
        emit(setting_entry_row(entry), fmt=fmt, columns=columns)


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
        resolved_value = resolve_set_value(
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


__all__ = ["build_config_app"]
