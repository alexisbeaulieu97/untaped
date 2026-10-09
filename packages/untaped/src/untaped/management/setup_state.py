"""What ``untaped setup`` and ``untaped setup plan`` read about a profile's services.

A service is a composed capability whose profile model has ``base_url`` and
``token`` fields. Both commands resolve each one's current state here, the
way ``doctor`` does (the profile's values with ``UNTAPED_*`` overrides
layered on top), so the setup screen, the plan and doctor cannot disagree about
what is set up.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from untaped.auth import describe_token_source
from untaped.capabilities.registry import CapabilitySpec, CompositionResult
from untaped.doctor_checks import service_configured
from untaped.errors import ConfigError, UsageError
from untaped.management.auth import inherited_from_default, plaintext_token
from untaped.messages import not_found
from untaped.profile_resolver import DEFAULT_PROFILE
from untaped.settings import active_settings_layout, check_settings_field

_SERVICE_FIELDS = frozenset({"base_url", "token"})


def setup_services(
    result: CompositionResult, only: list[str] | None = None
) -> dict[str, CapabilitySpec]:
    """The composed services by name, narrowed to ``only`` (comma-separated entries).

    Raises :class:`ConfigError` when no capability is a service and
    :class:`UsageError` for an ``only`` name that is not one.
    """
    services = {
        registered.spec.name: registered.spec
        for registered in result.capabilities
        if registered.spec.profile_model.model_fields.keys() >= _SERVICE_FIELDS
    }
    if not services:
        raise ConfigError("no composed capability takes a base URL and token to set up")
    if not only:
        return services
    names = [part.strip() for entry in only for part in entry.split(",") if part.strip()]
    for name in names:
        if name not in services:
            raise UsageError(not_found("service", name, known=services))
    return {name: services[name] for name in dict.fromkeys(names)}


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

    base_url: str | None
    """Effective URL (the model default when unset), the form's starting value."""

    token_source: str | None
    """Where the token would come from (``describe_token_source``), if anywhere."""

    configured: bool
    """Whether the section is set up (:func:`untaped.doctor_checks.service_configured`)."""

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


def service_state(
    spec: CapabilitySpec, node: object, own: dict[str, Any], profile: str, raw: dict[str, Any]
) -> ServiceState:
    """Resolve one service.

    ``node`` is its effective section, ``own`` the profile's own data and
    ``raw`` the whole config.
    """
    data = node if isinstance(node, dict) else {}
    stored = data.get("base_url")
    configured_url = stored if isinstance(stored, str) and stored.strip() else None
    section = spec.config_section
    plaintext = plaintext_token(own, section)
    try:
        settings: BaseModel = check_settings_field(section, data, model=spec.profile_model)
    except ConfigError as exc:
        return ServiceState(
            configured_url, None, configured_url is not None, plaintext, invalid=str(exc)
        )
    url = getattr(settings, "base_url", None)
    own_node = own.get(section)
    command = own_node.get("token_command") if isinstance(own_node, dict) else None
    own_command = [str(part) for part in command] if isinstance(command, list) else None
    return ServiceState(
        url if isinstance(url, str) and url else configured_url,
        describe_token_source(settings, section=section),
        service_configured(settings, section=section),
        plaintext,
        own_command or None,
        inherited_from_default(section, profile, "token", raw),
        inherited_from_default(section, profile, "token_command", raw),
    )


def service_states(
    services: dict[str, CapabilitySpec], profile: str, raw: dict[str, Any]
) -> dict[str, ServiceState]:
    """Every service's state in ``profile``, from the parsed config ``raw``."""
    values = profile_view(raw, profile)
    own = active_settings_layout().profile_data(raw, profile) or {}
    return {
        name: service_state(spec, values.get(spec.config_section), own, profile, raw)
        for name, spec in services.items()
    }


__all__ = ["ServiceState", "profile_view", "service_state", "service_states", "setup_services"]
