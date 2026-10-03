"""What ``untaped setup`` and ``untaped setup plan`` read about a profile's services.

A service is a composed capability whose profile model has ``base_url`` and
``token`` fields. Both commands resolve each one's current state here, so
the wizard and the plan cannot disagree about what is set up.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from untaped.auth import describe_token_source
from untaped.capabilities.registry import CapabilitySpec, CompositionResult
from untaped.doctor_checks import service_configured
from untaped.errors import ConfigError, UsageError
from untaped.messages import not_found
from untaped.profile_resolver import DEFAULT_PROFILE
from untaped.settings import active_settings_layout

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
    """Effective URL (the model default when unset), offered as the prompt default."""

    token_source: str | None
    """Where the token would come from (``describe_token_source``), if anywhere."""

    configured: bool
    """Whether the section is set up (:func:`untaped.doctor_checks.service_configured`)."""

    plaintext: str | None = None
    """The token stored in plain text in the config, if any."""

    invalid: str | None = None
    """The validation error when the section's settings are invalid."""


def service_state(spec: CapabilitySpec, node: object) -> ServiceState:
    """Resolve one service section's ``node`` (its effective profile values)."""
    data = node if isinstance(node, dict) else {}
    stored = data.get("base_url")
    configured_url = stored if isinstance(stored, str) and stored.strip() else None
    token = data.get("token")
    plaintext = token.strip() if isinstance(token, str) and token.strip() else None
    try:
        settings: BaseModel = spec.profile_model.model_validate(data)
    except ValidationError as exc:
        return ServiceState(
            configured_url,
            None,
            configured_url is not None,
            plaintext,
            invalid=str(exc.errors()[0]["msg"]) if exc.errors() else str(exc),
        )
    url = getattr(settings, "base_url", None)
    section = spec.config_section
    return ServiceState(
        url if isinstance(url, str) and url else configured_url,
        describe_token_source(settings, section=section),
        service_configured(settings, section=section),
        plaintext,
    )


__all__ = ["ServiceState", "profile_view", "service_state", "setup_services"]
