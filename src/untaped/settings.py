"""Registry-backed configuration loaded from ``~/.untaped/config.yml``.

The SDK owns YAML/env loading and an in-process registry that the running tool
populates (via :func:`untaped.tool.register_tool`) with its typed settings
section(s) and the built-in profiles layout.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import BaseModel, Field, ValidationError, create_model
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)
from pydantic_settings.sources import InitSettingsSource

from untaped.errors import ConfigError, first_validation_error
from untaped.settings_layout import ProfilesSettingsLayout
from untaped.theme import UiSettings

DEFAULT_CONFIG_PATH = "~/.untaped/config.yml"


class HttpSettings(BaseModel):
    """Cross-cutting HTTP behaviour for a tool's HTTP client (per-profile)."""

    ca_bundle: Path | None = None
    verify_ssl: bool = True
    verify_hostname: bool = True
    timeout: float = Field(default=30.0, gt=0)
    proxy: str | None = None


#: Built-in top-level *state* sections (tool-managed runtime data spliced in
#: regardless of profile). ``http``/``ui`` used to live here but are now ordinary
#: per-profile settings (base fields on :class:`Settings`); only a tool's own
#: ``state_model`` registers here at runtime.
BUILTIN_STATE_SECTIONS: dict[str, type[BaseModel]] = {}


class _ConfigRegistry:
    """Mutable in-process registry of the running tool's config sections."""

    def __init__(self) -> None:
        self.profile_sections: dict[str, type[BaseModel]] = {}
        self.state_sections: dict[str, type[BaseModel]] = {}

    def reset(self) -> None:
        self.profile_sections = {}
        self.state_sections = dict(BUILTIN_STATE_SECTIONS)
        _warned_legacy_config.clear()
        get_settings.cache_clear()
        get_settings_model.cache_clear()
        get_profile_settings_model.cache_clear()

    def register_profile_settings(self, section: str, model: type[BaseModel]) -> None:
        _reject_reserved_section(section)
        existing = self.profile_sections.get(section)
        if existing is not None and existing is not model:
            raise ConfigError(f"duplicate profile settings section: {section}")
        state_model = self.state_sections.get(section)
        if state_model is not None:
            validate_disjoint_settings_sections(section, model, state_model)
        self.profile_sections[section] = model
        get_settings.cache_clear()
        get_settings_model.cache_clear()
        get_profile_settings_model.cache_clear()

    def register_state_settings(self, section: str, model: type[BaseModel]) -> None:
        _reject_reserved_section(section)
        existing = self.state_sections.get(section)
        if existing is not None and existing is not model:
            raise ConfigError(f"duplicate state settings section: {section}")
        profile_model = self.profile_sections.get(section)
        if profile_model is not None:
            validate_disjoint_settings_sections(section, profile_model, model)
        self.state_sections[section] = model
        get_settings.cache_clear()
        get_settings_model.cache_clear()
        get_profile_settings_model.cache_clear()


_CONFIG_REGISTRY = _ConfigRegistry()

# Config paths already warned about (warn-once-per-process for the flat→profiles
# migration); keyed by (path, offending section names). Cleared on registry reset.
_warned_legacy_config: set[tuple[str, frozenset[str]]] = set()


class Settings(BaseSettings):
    """Base settings class; concrete aggregate models are built dynamically."""

    model_config = SettingsConfigDict(
        env_prefix="UNTAPED_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    log_level: str = "INFO"
    http: HttpSettings = Field(default_factory=HttpSettings)
    ui: UiSettings = Field(default_factory=UiSettings)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        path = resolve_config_path()
        return (
            init_settings,
            env_settings,
            LayoutSettingsSource(settings_cls, yaml_file=path),
            file_secret_settings,
        )


_PROFILES_LAYOUT = ProfilesSettingsLayout()


def active_settings_layout() -> ProfilesSettingsLayout:
    """Return the SDK's settings layout (the profiles layout, always)."""
    return _PROFILES_LAYOUT


def register_profile_settings(section: str, model: type[BaseModel]) -> None:
    """Register a tool's profile-scoped section (lives under ``profiles.<name>``)."""
    _CONFIG_REGISTRY.register_profile_settings(section, model)


def register_state_settings(section: str, model: type[BaseModel]) -> None:
    """Register a tool's top-level state section spliced into the effective config."""
    _CONFIG_REGISTRY.register_state_settings(section, model)


def validate_disjoint_settings_sections(
    section: str,
    profile_model: type[BaseModel],
    state_model: type[BaseModel],
) -> None:
    """Reject profile/state models whose fields would compete for precedence."""
    overlap = sorted(profile_model.model_fields.keys() & state_model.model_fields.keys())
    if overlap:
        joined = ", ".join(overlap)
        raise ConfigError(f"overlapping profile/state settings for section {section!r}: {joined}")


def _reject_reserved_section(section: str) -> None:
    """Reject a tool section name that collides with an SDK base field.

    ``log_level``/``http``/``ui`` are base fields on :class:`Settings`;
    registering a tool section with one of those names would shadow the SDK
    field in the dynamically built model and break config resolution.
    """
    if section in Settings.model_fields:
        raise ConfigError(f"reserved SDK settings section: {section!r}")


def reset_config_registry_for_tests() -> None:
    """Reset the running tool's registered config sections.

    Public only for test isolation. Production code registers sections via
    :func:`untaped.tool.register_tool`.
    """
    _CONFIG_REGISTRY.reset()


class LayoutSettingsSource(InitSettingsSource):
    """Pydantic-settings source reading YAML through the active settings layout."""

    def __init__(self, settings_cls: type[BaseSettings], yaml_file: Path) -> None:
        raw = self._load_raw_yaml(yaml_file)
        effective = active_settings_layout().effective(raw)
        splice_registered_state(raw, effective)
        _warn_on_legacy_flat_sections(raw, yaml_file)
        super().__init__(settings_cls, effective)

    @staticmethod
    def _load_raw_yaml(yaml_file: Path) -> dict[str, Any]:
        if not yaml_file.is_file():
            return {}
        try:
            with yaml_file.open() as f:
                raw = yaml.safe_load(f)
        except yaml.YAMLError as exc:
            raise ConfigError(f"could not parse {yaml_file}: {exc}") from exc
        return raw if isinstance(raw, dict) else {}


def splice_registered_state(raw: Mapping[str, Any], effective: dict[str, Any]) -> None:
    """Merge registered top-level state sections into an effective profile dict."""
    for section, model in _CONFIG_REGISTRY.state_sections.items():
        state = raw.get(section)
        if not isinstance(state, dict):
            continue
        try:
            state_data = model.model_validate(state).model_dump(exclude_unset=True)
        except ValidationError as exc:
            path = resolve_config_path()
            raise ConfigError(
                f"invalid top-level config section {section!r} in {path}: "
                f"{first_validation_error(exc)}"
            ) from exc
        merged = effective.setdefault(section, {})
        if isinstance(merged, dict):
            merged.update(state_data)
        else:
            effective[section] = state_data


def _warn_on_legacy_flat_sections(raw: Mapping[str, Any], path: Path) -> None:
    """Warn once when a per-profile setting sits at the config top level.

    The flat top-level layout was removed in v1.0.1 (and ``http``/``ui`` joined
    it in v2.0.0); such a key is now silently ignored by the profiles resolver.
    The set of "belongs under a profile" keys is derived from the schema — the
    base per-profile fields (``log_level``, ``http``, ``ui``) plus every
    registered tool section — so there's no hard-coded name list. Tool-managed
    top-level *state* sections legitimately stay top-level and are excluded.
    """
    profile_keys = set(Settings.model_fields) | set(_CONFIG_REGISTRY.profile_sections)
    offending = sorted(
        key for key in raw if key in profile_keys and key not in _CONFIG_REGISTRY.state_sections
    )
    if not offending:
        return
    cache_key = (str(path), frozenset(offending))
    if cache_key in _warned_legacy_config:
        return
    _warned_legacy_config.add(cache_key)
    sections = ", ".join(offending)
    logging.getLogger("untaped").warning(
        "%s has top-level section(s) %s that are ignored since the v1.0.1 profiles "
        "layout. Move them under `profiles.default.<section>` "
        "(e.g. `profiles:` → `default:` → `%s: ...`).",
        path,
        sections,
        offending[0],
    )


def resolve_config_path() -> Path:
    """Return the active config file path."""
    return Path(os.environ.get("UNTAPED_CONFIG", DEFAULT_CONFIG_PATH)).expanduser()


@lru_cache(maxsize=1)
def get_settings_model() -> type[Settings]:
    """Build the current aggregate settings model from registered sections."""
    return _build_settings_model(_CONFIG_REGISTRY.profile_sections, _CONFIG_REGISTRY.state_sections)


@lru_cache(maxsize=1)
def get_profile_settings_model() -> type[Settings]:
    """Build the user-tunable profile settings model without top-level state."""
    return _build_settings_model(_CONFIG_REGISTRY.profile_sections, {})


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached aggregate settings instance."""
    path = resolve_config_path()
    try:
        return get_settings_model()()
    except ValidationError as exc:
        raise ConfigError(f"invalid config in {path}: {first_validation_error(exc)}") from exc


def get_core_settings() -> Settings:
    """Alias for callers that want to emphasize core-only settings access."""
    return get_settings()


def get_config_section[T: BaseModel](section: str, model_cls: type[T]) -> T:
    """Return one typed settings section, registering a one-off model if needed."""
    registered = _CONFIG_REGISTRY.profile_sections.get(section)
    if registered is None:
        temp_model = _build_settings_model({section: model_cls}, _CONFIG_REGISTRY.state_sections)
        settings = _instantiate(temp_model)
    else:
        settings = get_settings()
    value = getattr(settings, section)
    if isinstance(value, model_cls):
        return value
    if isinstance(value, BaseModel):
        return model_cls.model_validate(value.model_dump())
    return model_cls.model_validate(value)


def _instantiate(settings_cls: type[Settings]) -> Settings:
    path = resolve_config_path()
    try:
        return settings_cls()
    except ValidationError as exc:
        raise ConfigError(f"invalid config in {path}: {first_validation_error(exc)}") from exc


def _build_settings_model(
    profile_sections: Mapping[str, type[BaseModel]],
    state_sections: Mapping[str, type[BaseModel]],
) -> type[Settings]:
    fields: dict[str, Any] = {}
    for section in [*profile_sections, *state_sections]:
        model = _section_model(section, profile_sections, state_sections)
        fields.setdefault(section, (model, Field(default_factory=model)))
    return cast(
        "type[Settings]",
        create_model("UntapedSettings", __base__=Settings, **fields),
    )


def _section_model(
    section: str,
    profile_sections: Mapping[str, type[BaseModel]],
    state_sections: Mapping[str, type[BaseModel]],
) -> type[BaseModel]:
    profile_model = profile_sections.get(section)
    state_model = state_sections.get(section)
    if profile_model is None:
        if state_model is None:
            raise ConfigError(f"config section {section!r} is not registered")
        return state_model
    if state_model is None or state_model is profile_model:
        return profile_model
    return cast(
        "type[BaseModel]",
        create_model(f"{section.title()}Settings", __base__=(profile_model, state_model)),
    )


def validate_settings_isolated(
    data: dict[str, Any], settings_cls: type[Settings] | None = None
) -> Settings:
    """Validate ``data`` against the current schema without reading disk/env."""
    target_cls = settings_cls or get_settings_model()

    def _init_only(
        cls: type[Settings],
        settings_cls: type[Settings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings,)

    validator_cls = cast(
        "type[Settings]",
        type(
            "_ValidateOnly",
            (target_cls,),
            {"settings_customise_sources": classmethod(_init_only)},
        ),
    )
    return validator_cls.model_validate(data)


reset_config_registry_for_tests()
