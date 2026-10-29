"""What ``untaped setup`` and ``untaped setup plan`` read about a profile's services.

A service is a composed plugin whose settings mark what needs setting up
(:mod:`untaped.sdk.health.marks`), or that declares a credential. Both
commands resolve each one's current state here, with the set rules doctor's
``setup`` row uses
(:mod:`untaped.sdk.health._setup_status`: the profile's values with ``UNTAPED_*``
overrides layered on top), so the setup screen, the plan and doctor cannot
disagree about what is set up.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from untaped.core.config.repository import SettingsFileRepository
from untaped.core.config.use_cases import SetSetting
from untaped.core.profile.repository import ProfileFileRepository
from untaped.runtime.config_file import read_config_dict
from untaped.runtime.profile_resolver import DEFAULT_PROFILE
from untaped.runtime.registry import CompositionResult, settings_model
from untaped.runtime.settings import (
    active_settings_layout,
    check_settings_field,
    profile_section_models,
)
from untaped.sdk._schema import walk_settings
from untaped.sdk.errors import ConfigError, UsageError
from untaped.sdk.health._setup_status import (
    field_value,
    plugin_marks,
    secret_source,
    shown_value,
    unset_fields,
)
from untaped.sdk.health.marks import MarkedField, SetupMarks
from untaped.sdk.network._token_store import TokenStore, pick_store
from untaped.sdk.words import not_found

if TYPE_CHECKING:
    from untaped.runtime.composed import ComposedPlugin


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
) -> dict[str, ComposedPlugin]:
    """The composed services by name (empty when none), narrowed to the sections in ``only``.

    Raises :class:`UsageError` for an ``only`` name that is not a section.
    """
    services = {
        plugin.name: plugin for plugin in result.plugins if plugin_marks(plugin) is not None
    }
    if only:
        services = {name: services[name] for name in setup_sections(only) if name in services}
    return services


def service_marks(plugin: ComposedPlugin) -> SetupMarks:
    """What service ``plugin`` marks, and the credentials it declares."""
    marks = plugin_marks(plugin)
    assert marks is not None, f"{plugin.name} is not a service"
    return marks


def form_fields(plugin: ComposedPlugin) -> tuple[MarkedField, ...]:
    """The settings the setup form edits: every marked setting, each in its shape's field.

    A marked model (a :class:`~untaped.sdk.network.credentials.CredentialKey` such as
    ``server``) is edited as the fields it needs, those without a default
    (``server.url``), written together; the others keep rows of their own.
    A credential is no setting: the form's source tabs file it.
    """
    descriptors = walk_settings(settings_model(plugin), include_collections=True)
    fields: list[MarkedField] = []
    for marked in service_marks(plugin).required:
        if marked.is_secret:
            continue
        size = len(marked.path)
        for descriptor in descriptors:
            if descriptor.path == marked.path:
                fields.append(marked)
            elif descriptor.path[:size] == marked.path and not descriptor.has_default:
                fields.append(MarkedField(descriptor.path))
    return tuple(fields)


def connection_keys(plugin: ComposedPlugin) -> frozenset[str]:
    """The keys of ``plugin``'s connection, which setup edits together as one row."""
    return frozenset(f"{plugin.name}.{field.key}" for field in form_fields(plugin))


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

    values: Mapping[str, object] = field(repr=False)
    """Each form field's effective value (the model default when unset; ``None`` for none),
    the form's starting values (never in a repr: a masked setting is among them)."""

    own: Mapping[str, object] = field(default_factory=dict, repr=False)
    """Each form field's raw value in the profile's own data (``None`` when it sets none):
    what a save expects to find, so a value changed since the form opened is not replaced."""

    secret_sources: Mapping[str, str] = field(default_factory=dict)
    """Where each secret would come from, by name, for those found anywhere."""

    missing: tuple[str, ...] = ()
    """The marked fields still unset, by key within the section."""

    required: int = 0
    """How many fields the plugin marks as needing setup."""

    invalid: str | None = None
    """Why the section's settings (file plus environment) are invalid, if they are."""

    shown: Mapping[str, str] = field(default_factory=dict)
    """Each set marked field neither secret nor masked, as text (``setup plan``'s done rows)."""

    sources: Mapping[str, str] = field(default_factory=dict)
    """Where each set secret comes from (``stored 9b07e4``, ``$HOOK_KEY``)."""

    token_only: bool = False
    """Whether the ``token`` secret is the one marked field still unset."""

    @property
    def configured(self) -> bool:
        """Whether every marked field is set."""
        return self.invalid is None and not self.missing


def service_state(plugin: ComposedPlugin, node: object, profile: str) -> ServiceState:
    """Resolve one service in ``profile``; ``node`` is its effective section."""
    data = node if isinstance(node, dict) else {}
    section = plugin.name
    marks = service_marks(plugin)
    form = form_fields(plugin)
    required = len(marks.required)
    current = SetSetting(SettingsFileRepository()).current
    owned = {each.key: current(f"{section}.{each.key}", profile=profile) for each in form}
    try:
        settings: BaseModel = check_settings_field(section, data, model=settings_model(plugin))
    except ConfigError as exc:
        values = {each.key: _at(data, each.path) for each in form}
        missing = tuple(each.key for each in marks.required)
        return ServiceState(
            values,
            own=owned,
            missing=missing,
            required=required,
            invalid=str(exc),
        )
    token = marks.token
    missing = tuple(each.key for each in unset_fields(settings, marks))
    shown = {
        each.key: shown_value(settings, each)
        for each in marks.required
        if not each.is_secret and not each.masked and field_value(settings, each) is not None
    }
    found = {
        each.key: source
        for each in marks.secrets
        if (source := secret_source(settings, each)) is not None
    }
    return ServiceState(
        {each.key: field_value(settings, each) for each in form},
        own=owned,
        secret_sources=found,
        missing=missing,
        required=required,
        shown=shown,
        sources={key: source for key, source in found.items() if key not in missing},
        token_only=token is not None and missing == (token.key,),
    )


def _at(data: dict[str, Any], path: tuple[str, ...]) -> object:
    """The raw value at ``path`` in ``data`` (``None`` where a level is missing)."""
    value: object = data
    for part in path:
        value = value.get(part) if isinstance(value, dict) else None
    return value


def service_states(
    services: dict[str, ComposedPlugin], profile: str, raw: dict[str, Any]
) -> dict[str, ServiceState]:
    """Every service's state in ``profile``, from the parsed config ``raw``."""
    values = profile_view(raw, profile)
    return {
        name: service_state(plugin, values.get(plugin.name), profile)
        for name, plugin in services.items()
    }


def read_states(services: dict[str, ComposedPlugin], profile: str) -> dict[str, ServiceState]:
    """Every service's state in ``profile``, read from the config file now."""
    return service_states(services, profile, read_config_dict())


def profile_exists(profile: str) -> bool:
    """Whether ``profile`` exists (``default`` always does)."""
    return profile == DEFAULT_PROFILE or ProfileFileRepository().read(profile) is not None


def service_store(services: dict[str, ComposedPlugin]) -> TokenStore | None:
    """The store a secret goes to, probed once per run; ``None`` when no service stores one.

    A service stores one when its plugin declares a credential. One probe
    per run: a dead Secret Service costs its timeout once.
    """
    if any(plugin.credentials for plugin in services.values()):
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
