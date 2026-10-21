"""What ``untaped setup`` and ``untaped setup plan`` read about a profile's services.

A service is a composed plugin whose settings mark what needs setting up
(:mod:`untaped.setting_marks`), or, deprecated, an unmarked one with
``base_url`` and ``token`` fields. Both commands resolve each one's current
state here, with the set rules doctor's ``setup`` row uses
(:mod:`untaped.setup_status`: the profile's values with ``UNTAPED_*``
overrides layered on top), so the setup screen, the plan and doctor cannot
disagree about what is set up.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, SecretStr

from untaped.auth import describe_token_source, takes_token_command
from untaped.config_file import read_config_dict
from untaped.config_schema import walk_settings
from untaped.doctor_checks import service_configured
from untaped.errors import ConfigError, UsageError
from untaped.management.auth import inherited_from_default, plaintext_token
from untaped.messages import not_found
from untaped.plugins.registry import CompositionResult, PluginSpec, settings_model
from untaped.profile.repository import ProfileFileRepository
from untaped.profile_resolver import DEFAULT_PROFILE
from untaped.setting_marks import MarkedField, SetupMarks
from untaped.settings import (
    active_settings_layout,
    check_settings_field,
    profile_section_models,
)
from untaped.setup_status import (
    field_value,
    notify_unmarked,
    plugin_marks,
    secret_source,
    unset_fields,
)
from untaped.token_store import TokenStore, pick_store


def setup_sections(only: list[str] | None = None) -> tuple[str, ...]:
    """The profile's settings sections ``setup`` lists, narrowed to ``only`` (comma-separated).

    Every section, in the settings model's order, without ``only``. Raises
    :class:`UsageError` for an ``only`` name that is not a section.
    """
    sections = tuple(profile_section_models())
    if not only:
        return sections
    names = [part.strip() for entry in only for part in entry.split(",") if part.strip()]
    for name in names:
        if name not in sections:
            raise UsageError(not_found("section", name, known=sections))
    return tuple(dict.fromkeys(names))


def setup_services(
    result: CompositionResult, only: list[str] | None = None
) -> dict[str, PluginSpec]:
    """The composed services by name (empty when none), narrowed to the sections in ``only``.

    Each one set up through the legacy fallback warns, once per run
    (:data:`~untaped.setting_marks.UNMARKED_SERVICE`). Raises
    :class:`UsageError` for an ``only`` name that is not a section.
    """
    services = {
        registered.spec.name: registered.spec
        for registered in result.plugins
        if plugin_marks(registered.spec) is not None
    }
    if only:
        services = {name: services[name] for name in setup_sections(only) if name in services}
    for name, spec in services.items():
        notify_unmarked(name, plugin_marks(spec))
    return services


def service_marks(spec: PluginSpec) -> SetupMarks:
    """What service ``spec`` marks (or the legacy fallback reads as marked)."""
    marks = plugin_marks(spec)
    assert marks is not None, f"{spec.name} is not a service"
    return marks


def form_fields(spec: PluginSpec) -> tuple[MarkedField, ...]:
    """The marked fields the connection form edits as text: top-level, scalar, not secret.

    Any other marked field (a list, a nested one, a secret other than
    ``token``) stays a row of its own.
    """
    descriptors = {d.path: d for d in walk_settings(settings_model(spec))}
    return tuple(
        field
        for field in service_marks(spec).required
        if field.secret is None
        and len(field.path) == 1
        and (descriptor := descriptors.get(field.path)) is not None
        and not descriptor.is_secret
    )


def connection_keys(spec: PluginSpec) -> frozenset[str]:
    """The keys of ``spec``'s connection, which setup edits together as one row."""
    keys = [field.key for field in form_fields(spec)]
    token = service_marks(spec).token
    if token is not None:
        keys.append(token.key)
        if token.command is not None and token.command in settings_model(spec).model_fields:
            keys.append(token.command)
    return frozenset(f"{spec.name}.{key}" for key in keys)


def profile_view(raw: dict[str, Any], profile: str) -> dict[str, Any]:
    """The profile's effective values (a new profile starts from ``default``)."""
    layout = active_settings_layout()
    for candidate in (profile, DEFAULT_PROFILE):
        try:
            return layout.effective(raw, profile=candidate)
        except ConfigError:
            continue
    return {}


@dataclass(frozen=True)
class ServiceState:
    """What a service section currently resolves to in the profile."""

    values: Mapping[str, str]
    """Each form field's effective value (the model default when unset; ``""`` for none),
    the form's starting values."""

    token_source: str | None
    """Where the ``token`` would come from (``describe_token_source``), if anywhere."""

    missing: tuple[str, ...] = ()
    """The marked fields still unset, by key within the section."""

    required: int = 0
    """How many fields the plugin marks as needing setup."""

    plaintext: str | None = field(default=None, repr=False)
    """The token stored in plain text in the profile's own config, if any (never in a repr)."""

    own_command: list[str] | None = None
    """The profile's own ``token_command``, if any."""

    inherited_token: bool = False
    """Whether ``default``'s plaintext token wins over anything set in the profile."""

    inherited_command: bool = False
    """Whether ``default`` has a ``token_command``, which applies once the profile's is gone."""

    invalid: str | None = None
    """Why the section's settings (file plus environment) are invalid, if they are."""

    shown: Mapping[str, str] = field(default_factory=dict)
    """Each marked field that is not a secret, set, as text (for ``setup plan``'s done rows)."""

    sources: Mapping[str, str] = field(default_factory=dict)
    """Where each set secret comes from (``hook.signing_key_command``, ``$HOOK_KEY``)."""

    token_only: bool = False
    """Whether the ``token`` secret is the one marked field still unset."""

    legacy_configured: bool | None = None
    """For a plugin read through the legacy fallback, whether it is set up by the old rule
    (:func:`~untaped.doctor_checks.service_configured`); ``None`` for a marked one."""

    @property
    def configured(self) -> bool:
        """Whether every marked field is set."""
        return self.invalid is None and not self.missing


def service_state(
    spec: PluginSpec, node: object, own: dict[str, Any], profile: str, raw: dict[str, Any]
) -> ServiceState:
    """Resolve one service.

    ``node`` is its effective section, ``own`` the profile's own data and
    ``raw`` the whole config.
    """
    data = node if isinstance(node, dict) else {}
    section = spec.name
    marks = service_marks(spec)
    form = form_fields(spec)
    plaintext = plaintext_token(own, section)
    required = len(marks.required)
    try:
        settings: BaseModel = check_settings_field(section, data, model=settings_model(spec))
    except ConfigError as exc:
        values = {each.key: _text(data.get(each.key)) for each in form}
        missing = tuple(each.key for each in marks.required)
        return ServiceState(values, None, missing, required, plaintext, invalid=str(exc))
    own_node = own.get(section)
    command = own_node.get("token_command") if isinstance(own_node, dict) else None
    own_command = [str(part) for part in command] if isinstance(command, list) else None
    token = marks.token
    missing = tuple(each.key for each in unset_fields(settings, marks, section=section))
    shown = {
        each.key: _text(value)
        for each in marks.required
        if each.secret is None and (value := field_value(settings, each)) is not None
    }
    return ServiceState(
        {each.key: _text(field_value(settings, each)) for each in form},
        None if token is None else describe_token_source(settings, section=section),
        missing,
        required,
        plaintext,
        own_command or None,
        inherited_from_default(section, profile, "token", raw),
        inherited_from_default(section, profile, "token_command", raw),
        shown=shown,
        sources={
            each.key: source
            for each in marks.secrets
            if each.key not in missing
            and (source := secret_source(settings, each, section=section)) is not None
        },
        token_only=token is not None and missing == (token.key,),
        legacy_configured=service_configured(settings, section=section) if marks.legacy else None,
    )


def _text(value: object) -> str:
    if value is None:
        return ""
    return "***" if isinstance(value, SecretStr) else str(value)


def service_states(
    services: dict[str, PluginSpec], profile: str, raw: dict[str, Any]
) -> dict[str, ServiceState]:
    """Every service's state in ``profile``, from the parsed config ``raw``."""
    values = profile_view(raw, profile)
    own = active_settings_layout().profile_data(raw, profile) or {}
    return {
        name: service_state(spec, values.get(spec.name), own, profile, raw)
        for name, spec in services.items()
    }


def read_states(services: dict[str, PluginSpec], profile: str) -> dict[str, ServiceState]:
    """Every service's state in ``profile``, read from the config file now."""
    return service_states(services, profile, read_config_dict())


def profile_exists(profile: str) -> bool:
    """Whether ``profile`` exists (``default`` always does)."""
    return profile == DEFAULT_PROFILE or ProfileFileRepository().read(profile) is not None


def service_store(services: dict[str, PluginSpec]) -> TokenStore | None:
    """The store a token goes to, probed once per run; ``None`` when no service takes a command.

    One probe per run: a dead Secret Service costs its timeout once.
    """
    if any(takes_token_command(settings_model(spec)) for spec in services.values()):
        return pick_store()
    return None


__all__ = [
    "ServiceState",
    "connection_keys",
    "form_fields",
    "profile_exists",
    "profile_view",
    "read_states",
    "service_marks",
    "service_state",
    "service_states",
    "service_store",
    "setup_sections",
    "setup_services",
]
