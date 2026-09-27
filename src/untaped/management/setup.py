"""Root ``untaped setup`` command: an interactive wizard for one profile.

It asks which profile to configure (creating it when new), which service
capabilities to set up — every composed capability whose profile model has
``base_url`` and ``token`` fields — then each one's URL and how to get its
token: typed in (stored like ``config set KEY --prompt``), a
``token_command``, or the current source kept. Writes go through the same
validated settings repository as ``config set``. Finally it runs the
selected capabilities' doctor checks against that profile, online ones
included, and exits 1 when any fails. It needs a terminal (exit 2 without
one).
"""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from typing import Any

from cyclopts import App
from pydantic import BaseModel, ValidationError

from untaped.auth import describe_token_source
from untaped.capabilities.registry import ApplicationSpec, CapabilitySpec, CompositionResult
from untaped.cli import ColumnsOption, FormatOption, create_app, report_errors
from untaped.config.repository import SettingsFileRepository
from untaped.config_file import read_config_dict
from untaped.errors import ConfigError
from untaped.management.doctor import collect_doctor_rows, report_check_rows
from untaped.profile.repository import ProfileFileRepository
from untaped.profile.use_cases import CreateProfile
from untaped.profile_resolver import (
    DEFAULT_PROFILE,
    effective_active_profile_name,
    reset_profile_override,
    set_profile_override,
)
from untaped.prompts import PromptChoice
from untaped.settings import active_settings_layout, get_settings
from untaped.theme import OutputFormat
from untaped.ui import UiContext, ui_context

_SERVICE_FIELDS = frozenset({"base_url", "token"})


def build_root_setup_app(*, shell: ApplicationSpec, result: CompositionResult) -> App:
    """Return the root ``setup`` terminal command for one composition."""
    app = create_app(name="setup", help="Configure a profile's services interactively.")

    @app.default
    def setup_command(*, fmt: FormatOption = "table", columns: ColumnsOption = None) -> None:
        """Pick services, enter their URLs and tokens, then check them online."""
        with report_errors():
            _run(shell, result, fmt=fmt, columns=columns)

    return app


def _run(
    shell: ApplicationSpec,
    result: CompositionResult,
    *,
    fmt: OutputFormat,
    columns: list[str] | None,
) -> None:
    ui = ui_context(strict=False)
    services = {
        registered.spec.name: registered.spec
        for registered in result.capabilities
        if registered.spec.profile_model.model_fields.keys() >= _SERVICE_FIELDS
    }
    if not services:
        raise ConfigError("no composed capability takes a base URL and token to set up")
    with ui.terminal(refusal="setup requires an interactive terminal"):
        raw = read_config_dict()
        profile = ui.text(
            "Profile to configure",
            default=effective_active_profile_name(raw) or DEFAULT_PROFILE,
        ).strip()
        values = _profile_view(raw, profile)
        current = {
            name: _current(spec, values.get(spec.config_section)) for name, spec in services.items()
        }
        selected = ui.multiselect(
            "Capabilities to configure",
            [PromptChoice(value=name, label=name) for name in services],
            defaults=[name for name, state in current.items() if state.configured],
        )
        if not selected:
            ui.message("info", "no capabilities selected; no changes made")
            return
        profiles = ProfileFileRepository()
        if profile != DEFAULT_PROFILE and profiles.read(profile) is None:
            CreateProfile(profiles)(profile)
            ui.success(f"created profile: {profile}")
        repo = SettingsFileRepository()
        for name in selected:
            _configure(ui, repo, services[name], profile, current[name])
    rows = _check_rows(shell, result, profile, frozenset(selected))
    report_check_rows(rows, op="setup", fmt=fmt, columns=columns)
    ui.success(f"profile {profile} is ready")


def _profile_view(raw: dict[str, Any], profile: str) -> dict[str, Any]:
    """The profile's effective values (a new profile starts from ``default``)."""
    layout = active_settings_layout()
    for candidate in (profile, DEFAULT_PROFILE):
        try:
            return layout.effective(raw, profile=candidate)
        except ConfigError:
            continue
    return {}


@dataclass(frozen=True)
class _ServiceState:
    """What a service section currently resolves to in the profile."""

    base_url: str | None
    """Effective URL (the model default when unset), offered as the prompt default."""

    token_source: str | None
    """Where the token would come from (``describe_token_source``), if anywhere."""

    configured: bool
    """Whether the profile sets a URL or a token source is available."""


def _current(spec: CapabilitySpec, node: object) -> _ServiceState:
    data = node if isinstance(node, dict) else {}
    stored = data.get("base_url")
    configured_url = stored if isinstance(stored, str) and stored.strip() else None
    try:
        settings: BaseModel = spec.profile_model.model_validate(data)
    except ValidationError:
        return _ServiceState(configured_url, None, configured_url is not None)
    url = getattr(settings, "base_url", None)
    source = describe_token_source(settings, section=spec.config_section)
    return _ServiceState(
        url if isinstance(url, str) and url else configured_url,
        source,
        configured_url is not None or source is not None,
    )


def _configure(
    ui: UiContext,
    repo: SettingsFileRepository,
    spec: CapabilitySpec,
    profile: str,
    current: _ServiceState,
) -> None:
    section = spec.config_section
    source = current.token_source
    url = ui.text(f"{spec.name} base URL", default=current.base_url)
    repo.set_value(f"{section}.base_url", url.strip(), profile=profile)
    choices = [PromptChoice(value="enter", label="Enter a token (stored in config.yml)")]
    if "token_command" in spec.profile_model.model_fields:
        choices.append(PromptChoice(value="command", label="Run a command that prints the token"))
    if source is not None:
        choices.insert(0, PromptChoice(value="keep", label=f"Keep the current token ({source})"))
    how = ui.select(f"{spec.name} token", choices, default="keep" if source else "enter")
    if how == "enter":
        repo.set_value(f"{section}.token", ui.secret(f"{spec.name} token"), profile=profile)
    elif how == "command":
        argv = shlex.split(ui.text(f"{spec.name} token command"))
        if not argv:
            raise ConfigError(f"{spec.name} token command is empty")
        repo.set_value(f"{section}.token_command", json.dumps(argv), profile=profile)
        # A stored token wins over the command; drop it so the command is used.
        repo.unset_value(f"{section}.token", profile=profile)


def _check_rows(
    shell: ApplicationSpec,
    result: CompositionResult,
    profile: str,
    selected: frozenset[str],
) -> list[dict[str, object]]:
    """The selected capabilities' doctor rows, online checks included, for ``profile``."""
    token = set_profile_override(profile)
    get_settings.cache_clear()
    try:
        rows = collect_doctor_rows(shell, result, online=True, capabilities=selected)
    finally:
        reset_profile_override(token)
        get_settings.cache_clear()
    return [row for row in rows if row["capability"] in selected]


__all__ = ["build_root_setup_app"]
