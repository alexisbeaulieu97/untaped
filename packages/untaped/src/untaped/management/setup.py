"""Root ``untaped setup`` command: an interactive wizard for one profile.

It asks which profile to configure (creating it when new), which service
capabilities to set up — every composed capability whose profile model has
``base_url`` and ``token`` fields — then each one's URL and how to get its
token: stored with this machine's password store (``untaped auth set``),
a plaintext token moved there, a ``token_command``, a conventional
environment variable, or the current source kept. Writes go through the same
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

from untaped.auth import describe_token_source, takes_token_command, token_env_names
from untaped.capabilities.registry import ApplicationSpec, CapabilitySpec, CompositionResult
from untaped.cli import ColumnsOption, FormatOption, create_app, report_errors
from untaped.config.repository import SettingsFileRepository
from untaped.config_file import read_config_dict
from untaped.doctor_checks import service_configured
from untaped.errors import ConfigError
from untaped.management.auth import (
    drop_token_command,
    inherited_from_default,
    plaintext_token,
    save_token,
)
from untaped.management.doctor import collect_doctor_rows, report_check_rows
from untaped.messages import hint
from untaped.profile.repository import ProfileFileRepository
from untaped.profile.use_cases import CreateProfile
from untaped.profile_resolver import (
    DEFAULT_PROFILE,
    effective_active_profile_name,
    profile_scope,
)
from untaped.prompts import PromptChoice
from untaped.settings import active_settings_layout
from untaped.theme import OutputFormat
from untaped.token_store import TokenStore, pick_store
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
        active = effective_active_profile_name(raw) or DEFAULT_PROFILE
        profile = ui.text("Profile to configure", default=active).strip()
        values = _profile_view(raw, profile)
        own = active_settings_layout().profile_data(raw, profile) or {}
        current = {
            name: _current(spec, values.get(spec.config_section), own, profile)
            for name, spec in services.items()
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
        # One probe per run: a dead Secret Service costs its timeout once.
        commands = any(takes_token_command(services[name].profile_model) for name in selected)
        store = pick_store() if commands else None
        for name in selected:
            _configure(ui, repo, services[name], profile, current[name], store)
    rows = _check_rows(shell, result, profile, frozenset(selected))
    report_check_rows(rows, op="setup", fmt=fmt, columns=columns)
    if profile == active:
        ui.success(f"profile {profile} is ready")
    else:
        ui.success(
            f"profile {profile} is ready; use it with `untaped --profile {profile} …` "
            f"or make it the default with `untaped profile use {profile}`"
        )


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
    """Whether the section is set up (:func:`untaped.doctor_checks.service_configured`)."""

    plaintext: str | None = None
    """The token stored in plain text in the profile's own config, if any."""

    own_command: list[str] | None = None
    """The profile's own ``token_command``, if any."""

    inherited_token: bool = False
    """Whether ``default``'s plaintext token wins over anything set in the profile."""

    inherited_command: bool = False
    """Whether ``default``'s ``token_command`` applies to the profile."""


def _current(
    spec: CapabilitySpec, node: object, own: dict[str, Any], profile: str
) -> _ServiceState:
    data = node if isinstance(node, dict) else {}
    stored = data.get("base_url")
    configured_url = stored if isinstance(stored, str) and stored.strip() else None
    try:
        settings: BaseModel = spec.profile_model.model_validate(data)
    except ValidationError:
        return _ServiceState(configured_url, None, configured_url is not None)
    url = getattr(settings, "base_url", None)
    section = spec.config_section
    own_node = own.get(section)
    command = own_node.get("token_command") if isinstance(own_node, dict) else None
    own_command = [str(part) for part in command] if isinstance(command, list) else None
    return _ServiceState(
        url if isinstance(url, str) and url else configured_url,
        describe_token_source(settings, section=section),
        service_configured(settings, section=section),
        plaintext_token(own, section),
        own_command or None,
        inherited_from_default(section, profile, "token"),
        own_command is None and inherited_from_default(section, profile, "token_command"),
    )


def _configure(
    ui: UiContext,
    repo: SettingsFileRepository,
    spec: CapabilitySpec,
    profile: str,
    current: _ServiceState,
    store: TokenStore | None,
) -> None:
    """Ask for one service's URL and token, validate every answer, then write."""
    section = spec.config_section
    url = ui.text(f"{spec.name} base URL", default=current.base_url).strip()
    has_command = takes_token_command(spec.profile_model)
    if has_command and current.inherited_token:
        ui.message(
            "info",
            f"{section}.token is set in profile {DEFAULT_PROFILE} and wins over any token "
            f"set in profile {profile}; move it out of {DEFAULT_PROFILE} to change it\n"
            f"{hint('auth migrate')}",
        )
    choices = _token_choices(spec, current, store, has_command=has_command)
    how = ui.select(f"{spec.name} token", choices, default=choices[0].value)
    token = ui.secret(f"{spec.name} token").strip() if how in ("store", "enter") else None
    argv = _token_command(ui, spec) if how == "command" else None
    repo.set_value(f"{section}.base_url", url, profile=profile)
    if how == "store" and token is not None and store is not None:
        save_token(repo, section, profile, token, store)
    elif how == "move" and current.plaintext is not None and store is not None:
        save_token(repo, section, profile, current.plaintext, store)
    elif how == "enter" and token is not None:
        repo.set_value(f"{section}.token", token, profile=profile)
    elif argv is not None:
        repo.set_value(f"{section}.token_command", json.dumps(argv), profile=profile)
        # A stored token wins over the command; drop it so the command is used.
        repo.unset_value(f"{section}.token", profile=profile)
    elif how == "env":
        _use_env(ui, repo, spec, profile, current)


def _use_env(
    ui: UiContext,
    repo: SettingsFileRepository,
    spec: CapabilitySpec,
    profile: str,
    current: _ServiceState,
) -> None:
    """Clear what would win over the conventional variable: the token and its command."""
    section = spec.config_section
    repo.unset_value(f"{section}.token", profile=profile)
    if current.own_command is not None:
        where = drop_token_command(repo, section, profile, current.own_command)
        dropped = f"deleted the stored token from {where}" if where else "unset the command"
        ui.message("info", f"{section}.token_command no longer applies; {dropped}")
    env = token_env_names(spec.profile_model.model_construct())[0]
    ui.message("info", f"export ${env} in your shell for untaped {spec.name} to use it")


def _token_choices(
    spec: CapabilitySpec,
    current: _ServiceState,
    store: TokenStore | None,
    *,
    has_command: bool,
) -> list[PromptChoice[str]]:
    """The token choices, the recommended one first; none stores plain text if avoidable.

    While ``default``'s plaintext token wins, keeping it is the only choice
    that would work for a section ``auth`` serves.
    """
    choices: list[PromptChoice[str]] = []
    if current.token_source is not None:
        label = f"Keep the current token ({current.token_source})"
        choices.append(PromptChoice(value="keep", label=label))
    if has_command and current.inherited_token:
        return choices
    if current.plaintext is not None and store is not None:
        label = f"Move the current token to {store.name}"
        choices.insert(0, PromptChoice(value="move", label=label))
    if store is not None:
        choices.append(PromptChoice(value="store", label=f"Store a token with {store.name}"))
    if has_command:
        choices.append(PromptChoice(value="command", label="Run a command that prints the token"))
        env = token_env_names(spec.profile_model.model_construct())
        if env and not current.inherited_command:
            label = f"Use ${env[0]} (you export it; nothing is stored)"
            choices.append(PromptChoice(value="env", label=label))
    else:
        choices.append(PromptChoice(value="enter", label="Enter a token (stored in config.yml)"))
    return choices


def _token_command(ui: UiContext, spec: CapabilitySpec) -> list[str]:
    """Read and check a token command before anything is written for the service."""
    try:
        argv = shlex.split(ui.text(f"{spec.name} token command"))
    except ValueError as exc:
        raise ConfigError(f"invalid {spec.name} token command: {exc}", category="invalid") from exc
    if not argv:
        raise ConfigError(f"{spec.name} token command is empty", category="invalid")
    return argv


def _check_rows(
    shell: ApplicationSpec,
    result: CompositionResult,
    profile: str,
    selected: frozenset[str],
) -> list[dict[str, object]]:
    """The selected capabilities' doctor rows, online checks included, for ``profile``."""
    with profile_scope(profile):
        rows = collect_doctor_rows(shell, result, online=True, capabilities=selected)
    return [row for row in rows if row["capability"] in selected]


__all__ = ["build_root_setup_app"]
