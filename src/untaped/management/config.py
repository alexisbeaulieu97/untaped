"""Root ``untaped config …`` command group (Wave 1.4, spec §4).

Converts the per-tool config surface (:mod:`untaped.config.app`) to root
commands. The cyclopts wiring mirrors the per-tool group, but key resolution
is direct instead of delegated per tool: a fully qualified ``section.key``
selects its schema by ``section`` — SDK roots (``log_level``, ``http``,
``ui``) win first, then the section's own state fields raise the
"managed by" error, and anything else passes through to the schema. Bare
keys are never implicitly expanded to a capability section.

All read/write logic (list/get/set/unset/edit) is imported from the existing
config modules; only :class:`RootConfigContext` (the root resolution rule)
is new. There is deliberately no nested ``config doctor`` here: config
diagnostics live at the root ``doctor`` command, where one broken section
cannot block the other rows.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated

from cyclopts import App, Parameter

from untaped.capabilities.registry import ApplicationSpec, CompositionResult
from untaped.cli import (
    ColumnsOption,
    FormatOption,
    create_app,
    emit,
    report_errors,
)
from untaped.config.editor import run_config_editor
from untaped.config.models import setting_entry_row
from untaped.config.prompting import resolve_set_value
from untaped.config.repository import SettingsFileRepository
from untaped.config.use_cases import GetSetting, ListAllProfilesSettings, ListSettings
from untaped.errors import ConfigError
from untaped.render import OutputFormat
from untaped.settings import Settings, resolve_config_path
from untaped.ui import ui_context


@dataclass(frozen=True)
class RootSectionScope:
    """One section's key-resolution scope for the root config command."""

    capability: str
    """Owning capability name (the shell name for the shell section)."""

    profile_fields: frozenset[str]
    """User-tunable field names of the section's profile model."""

    state_fields: frozenset[str]
    """Tool-managed field names of the section's state model (never settable)."""


@dataclass(frozen=True)
class RootConfigContext:
    """Root key-resolution rule (spec §4, root half).

    SDK roots win first; a fully qualified ``section.key`` then resolves
    against that section's scope (state fields rejected); bare keys and
    unknown sections pass through untouched so the schema reports them.
    """

    sections: Mapping[str, RootSectionScope]

    def resolve_key(self, key: str) -> str:
        """Map a user key to the concrete config key without implicit expansion."""
        first, rest = _split_first(key)
        if first in Settings.model_fields:
            return key
        if rest is None:
            # Bare keys never expand to a capability section at the root.
            return key
        scope = self.sections.get(first)
        if scope is None:
            return key
        state_first, _ = _split_first(rest)
        if state_first in scope.state_fields:
            raise self._state_error(first, key)
        return key

    def _state_error(self, section: str, key: str) -> ConfigError:
        scope = self.sections[section]
        return ConfigError(
            f"{key!r} is managed by untaped {scope.capability} and is not a configurable setting"
        )


def _split_first(key: str) -> tuple[str, str | None]:
    first, sep, rest = key.partition(".")
    return first, rest if sep else None


def section_scopes(
    shell: ApplicationSpec, result: CompositionResult
) -> dict[str, RootSectionScope]:
    """Build the root resolution scopes for the shell plus composed capabilities."""
    scopes = {
        shell.config_section: RootSectionScope(
            capability=shell.name,
            profile_fields=frozenset(shell.profile_model.model_fields),
            state_fields=frozenset(
                shell.state_model.model_fields if shell.state_model is not None else ()
            ),
        )
    }
    for registered in result.capabilities:
        spec = registered.spec
        scopes[spec.config_section] = RootSectionScope(
            capability=spec.name,
            profile_fields=frozenset(spec.profile_model.model_fields),
            state_fields=frozenset(
                spec.state_model.model_fields if spec.state_model is not None else ()
            ),
        )
    return scopes


def build_root_config_app(*, shell: ApplicationSpec, result: CompositionResult) -> App:
    """Return the root ``config`` command group for one composition."""
    ctx = RootConfigContext(sections=section_scopes(shell, result))
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
        """List settings for every composed section (resolved effective values)."""
        _list(fmt=fmt, columns=columns, show_secrets=show_secrets, all_profiles=all_profiles)

    @app.command(name="get")
    def get_command(
        key: Annotated[str, Parameter(help="Fully qualified setting key (section.key).")],
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
        key: Annotated[str, Parameter(help="Fully qualified setting key (section.key).")],
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
            bool, Parameter(name="--prompt", help="Prompt for the value using the setting type.")
        ] = False,
    ) -> None:
        """Persist ``section.key = value`` (validated against the schema)."""
        _set(ctx, key, value, target_profile=target_profile, stdin=stdin, prompt=prompt)

    @app.command(name="unset")
    def unset_command(
        key: Annotated[str, Parameter(help="Fully qualified setting key (section.key).")],
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
        """Remove ``section.key`` from the resolved write scope (no-op if unset)."""
        _unset(ctx, key, target_profile=target_profile)

    @app.command(name="edit")
    def edit_command() -> None:
        """Open ``~/.untaped/config.yml`` in $VISUAL/$EDITOR and re-validate on save."""
        run_config_editor()

    return app


def _list(
    *,
    fmt: OutputFormat,
    columns: list[str] | None,
    show_secrets: bool,
    all_profiles: bool,
) -> None:
    with report_errors():
        repo = SettingsFileRepository()
        if all_profiles:
            entries = ListAllProfilesSettings(repo)(reveal_secrets=show_secrets)
        else:
            entries = ListSettings(repo)(reveal_secrets=show_secrets)
        rows = [setting_entry_row(e) for e in entries]
        emit(rows, fmt=fmt, columns=columns)


def _get(ctx: RootConfigContext, key: str, *, fmt: OutputFormat, show_secrets: bool) -> None:
    with report_errors():
        resolved = ctx.resolve_key(key)
        entry = GetSetting(SettingsFileRepository())(resolved, reveal_secrets=show_secrets)
        columns = ["value"] if fmt == "raw" else None
        emit(setting_entry_row(entry), fmt=fmt, columns=columns)


def _set(
    ctx: RootConfigContext,
    key: str,
    value: str | None,
    *,
    target_profile: str | None,
    stdin: bool,
    prompt: bool,
) -> None:
    with report_errors():
        repo = SettingsFileRepository()
        resolved = ctx.resolve_key(key)
        resolved_value = resolve_set_value(
            resolved, value, stdin=stdin, prompt=prompt, repo=repo, target_profile=target_profile
        )
        profile = repo.set_value(resolved, resolved_value, profile=target_profile)
        message = f"set {resolved} in profile {profile} (config: {resolve_config_path()})"
        ui_context(strict=False).message("success", message)


def _unset(ctx: RootConfigContext, key: str, *, target_profile: str | None) -> None:
    with report_errors():
        resolved = ctx.resolve_key(key)
        removed, profile = SettingsFileRepository().unset_value(resolved, profile=target_profile)
        ui = ui_context(strict=False)
        where = f"in profile {profile}"
        if removed:
            ui.message("success", f"unset {resolved} {where}")
        else:
            ui.message("info", f"{resolved} was not set {where}")


__all__ = [
    "RootConfigContext",
    "RootSectionScope",
    "build_root_config_app",
    "section_scopes",
]
