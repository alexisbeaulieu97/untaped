"""Registry-backed configuration loaded from ``~/.untaped/config.yml``.

Core owns profile resolution, YAML/env loading, and the registry that plugins
use to contribute typed settings sections. Plugins own their section models.
"""

from __future__ import annotations

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
from untaped.profile_resolver import effective_active_profile_name, resolve_profiles
from untaped.ui import UiSettings

DEFAULT_CONFIG_PATH = "~/.untaped/config.yml"


class HttpSettings(BaseModel):
    """Cross-cutting HTTP behaviour shared by every plugin client."""

    ca_bundle: Path | None = None
    verify_ssl: bool = True


class PluginInstallSpec(BaseModel):
    """One uv-installable plugin package spec."""

    spec: str
    editable: bool = False


class PluginToolSpec(BaseModel):
    """The uv tool package spec used when rebuilding the tool environment."""

    spec: str = "untaped"
    editable: bool = False


class PluginsState(BaseModel):
    """Top-level app state for desired plugin installations."""

    tool: PluginToolSpec = Field(default_factory=PluginToolSpec)
    packages: list[PluginInstallSpec] = Field(default_factory=list)


BUILTIN_STATE_SECTIONS: dict[str, type[BaseModel]] = {
    "plugins": PluginsState,
    "ui": UiSettings,
}


class _ConfigRegistry:
    """Mutable in-process registry of config sections contributed by plugins."""

    def __init__(self) -> None:
        self.profile_sections: dict[str, type[BaseModel]] = {}
        self.state_sections: dict[str, type[BaseModel]] = {}

    def reset(self) -> None:
        self.profile_sections = {}
        self.state_sections = dict(BUILTIN_STATE_SECTIONS)
        get_settings.cache_clear()
        get_settings_model.cache_clear()
        get_profile_settings_model.cache_clear()

    def register_profile_settings(self, section: str, model: type[BaseModel]) -> None:
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


class Settings(BaseSettings):
    """Base settings class; concrete aggregate models are built dynamically."""

    model_config = SettingsConfigDict(
        env_prefix="UNTAPED_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    log_level: str = "INFO"
    http: HttpSettings = Field(default_factory=HttpSettings)

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
            ProfilesSettingsSource(settings_cls, yaml_file=path),
            file_secret_settings,
        )


def register_profile_settings(section: str, model: type[BaseModel]) -> None:
    """Register a plugin-owned section that lives under ``profiles.<name>``."""
    _CONFIG_REGISTRY.register_profile_settings(section, model)


def register_state_settings(section: str, model: type[BaseModel]) -> None:
    """Register top-level plugin state spliced into the effective config."""
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


def reset_config_registry_for_tests() -> None:
    """Reset plugin-contributed config sections.

    Public only for tests and plugin loader isolation. Production code should
    register through :class:`untaped.plugins.PluginRegistry`.
    """
    _CONFIG_REGISTRY.reset()


class ProfilesSettingsSource(InitSettingsSource):
    """Pydantic-settings source for profile YAML plus top-level app state."""

    def __init__(self, settings_cls: type[BaseSettings], yaml_file: Path) -> None:
        raw = self._load_raw_yaml(yaml_file)
        effective, _ = resolve_profiles(raw, active_override=effective_active_profile_name(raw))
        splice_registered_state(raw, effective)
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
