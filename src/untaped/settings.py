"""Registry-backed configuration loaded from ``~/.untaped/config.yml``.

The unified composition root owns YAML/env loading and the in-process registry
of typed capability settings sections over the profiles layout. Capability
state lives in a separate ``state.yml`` (:func:`resolve_state_path`); a state
section still found at the top level of ``config.yml`` is read from there with
a one-time deprecation warning until its next write migrates it.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable, Mapping
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
from untaped.theme import CONFIG_WRITE_CONTEXT, UiSettings

DEFAULT_CONFIG_PATH = "~/.untaped/config.yml"
STATE_FILE_NAME = "state.yml"
STATE_PATH_ENV = "UNTAPED_STATE"


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
        check_state_section_name(section)
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


class _SettingsSources(BaseSettings):
    """Env + YAML-layout source wiring shared by every settings model.

    Split from :class:`Settings` so a single top-level section can be loaded
    on its own (:func:`load_settings_section`) without validating the rest.
    """

    model_config = SettingsConfigDict(
        env_prefix="UNTAPED_",
        env_nested_delimiter="__",
        extra="ignore",
    )

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


class Settings(_SettingsSources):
    """Base settings class; concrete aggregate models are built dynamically."""

    log_level: str = "INFO"
    http: HttpSettings = Field(default_factory=HttpSettings)
    ui: UiSettings = Field(default_factory=UiSettings)


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

    Public only for test isolation. Production code registers sections during
    unified composition.
    """
    _CONFIG_REGISTRY.reset()


class LayoutSettingsSource(InitSettingsSource):
    """Pydantic-settings source reading YAML through the active settings layout."""

    def __init__(self, settings_cls: type[BaseSettings], yaml_file: Path) -> None:
        raw = load_config_yaml(yaml_file)
        effective = active_settings_layout().effective(raw)
        # Only splice (and so only validate) the state sections this model
        # actually declares: a broken state section must not block loading
        # an unrelated one (see :func:`load_settings_section`).
        splice_registered_state(
            raw, effective, sections=settings_cls.model_fields, config_path=yaml_file
        )
        super().__init__(settings_cls, effective)


def load_config_yaml(yaml_file: Path) -> dict[str, Any]:
    """Parse the config file into a dict (``{}`` when absent or empty).

    Unreadable files, YAML syntax errors, and a non-mapping document root
    all raise :class:`ConfigError` naming the path.
    """
    if not yaml_file.is_file():
        return {}
    try:
        with yaml_file.open() as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise ConfigError(f"could not parse {yaml_file}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"could not read {yaml_file}: {exc.strerror or exc}") from exc
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(
            f"invalid config in {yaml_file}: the document root must be a mapping, "
            f"got {type(raw).__name__}"
        )
    return raw


def splice_registered_state(
    raw: Mapping[str, Any],
    effective: dict[str, Any],
    *,
    sections: Iterable[str] | None = None,
    config_path: Path | None = None,
) -> None:
    """Merge registered state sections into an effective profile dict.

    ``raw`` is the parsed ``config.yml`` (the legacy location); state is read
    from ``state.yml`` first (:func:`state_section_source`). ``sections``
    limits the splice to the named sections (default: all); ``state.yml`` is
    only read when at least one registered state section is wanted, so a
    broken state file never blocks loading a settings-only section.
    """
    wanted = None if sections is None else set(sections)
    targets = [
        (section, model)
        for section, model in _CONFIG_REGISTRY.state_sections.items()
        if wanted is None or section in wanted
    ]
    if not targets:
        return
    state_path = resolve_state_path()
    state_raw = load_config_yaml(state_path)
    legacy_path = config_path or resolve_config_path()
    for section, model in targets:
        found = state_section_source(
            section, state_raw, raw, state_path=state_path, config_path=legacy_path
        )
        if found is None or not isinstance(found[0], dict):
            continue
        state, source = found
        try:
            state_data = model.model_validate(state).model_dump(exclude_unset=True)
        except ValidationError as exc:
            raise ConfigError(
                f"invalid state section {section!r} in {source}: {first_validation_error(exc)}"
            ) from exc
        merged = effective.setdefault(section, {})
        if isinstance(merged, dict):
            merged.update(state_data)
        else:
            effective[section] = state_data


#: Top-level ``config.yml`` keys that are never capability state: moving one
#: into ``state.yml`` would drop the user's profiles or core settings.
RESERVED_STATE_SECTIONS = frozenset({"active", "profiles"})


def check_state_section_name(section: str) -> None:
    """Reject a state section name that collides with ``config.yml``'s own keys."""
    if not section or section in RESERVED_STATE_SECTIONS or section in Settings.model_fields:
        raise ConfigError(f"reserved or invalid state section name: {section!r}")


def state_section_source(
    section: str,
    state_raw: Mapping[str, Any],
    config_raw: Mapping[str, Any],
    *,
    state_path: Path,
    config_path: Path,
    warn: bool = True,
) -> tuple[Any, Path] | None:
    """Return ``(node, file)`` for one state section, or ``None`` when unset.

    ``state.yml`` wins whenever it has the section. Otherwise a legacy copy at
    the top level of ``config.yml`` is used (with a once-per-process
    deprecation warning unless ``warn`` is false) until the section's next
    state write moves it.
    """
    if section in state_raw:
        return state_raw[section], state_path
    if section not in config_raw:
        return None
    if warn:
        warn_legacy_state(config_path, state_path, section)
    return config_raw[section], config_path


_LEGACY_STATE_WARNED: set[Path] = set()


def warn_legacy_state(config_path: Path, state_path: Path, section: str) -> None:
    """Warn once per process (per config file) that state still lives in config.yml."""
    if config_path in _LEGACY_STATE_WARNED:
        return
    _LEGACY_STATE_WARNED.add(config_path)
    print(
        f"warning: capability state section {section!r} is still in {config_path}; "
        f"untaped now keeps state in {state_path}. It moves there automatically on "
        "its next state change (see `untaped doctor`).",
        file=sys.stderr,
    )


def resolve_state_path() -> Path:
    """Return the active state file path.

    ``UNTAPED_STATE`` wins; otherwise ``state.yml`` next to the resolved
    config file (``~/.untaped/state.yml`` by default). The state file must
    never be the config file itself.
    """
    config_path = resolve_config_path()
    override = os.environ.get(STATE_PATH_ENV, "").strip()
    path = Path(override).expanduser() if override else config_path.parent / STATE_FILE_NAME
    if path.resolve() == config_path.resolve():
        raise ConfigError(
            f"the state file {path} must not be the config file; "
            f"point {STATE_PATH_ENV} (or UNTAPED_CONFIG) elsewhere"
        )
    return path


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
    try:
        return get_settings_model()()
    except ValidationError as exc:
        raise ConfigError(settings_error_message(exc)) from exc


@lru_cache(maxsize=64)
def _section_models(
    settings_cls: type[Settings], name: str
) -> tuple[type[BaseModel], type[_SettingsSources]]:
    """Single-field models for ``name``: a plain validator and an env/YAML loader."""
    field = settings_cls.model_fields[name]
    definition: Any = (field.annotation, field)
    validator = create_model("UntapedSectionValidator", **{name: definition})
    loader = create_model("UntapedSectionSettings", __base__=_SettingsSources, **{name: definition})
    return validator, loader


def validate_settings_section(
    data: Mapping[str, Any], name: str, settings_cls: type[Settings] | None = None
) -> Any:
    """Validate only the top-level field ``name`` of ``data`` (no disk/env reads).

    A missing key validates the field's default (so a required field that is
    absent is reported). Write-time checks (``CONFIG_WRITE_CONTEXT``) run
    too, e.g. an unknown ``ui.theme`` is rejected. Raises :class:`pydantic.ValidationError`; error
    locations start with ``name``.
    """
    validator, _ = _section_models(settings_cls or get_settings_model(), name)
    payload = {name: data[name]} if name in data else {}
    validated = validator.model_validate(payload, context={CONFIG_WRITE_CONTEXT: True})
    return getattr(validated, name)


def load_settings_section(name: str, settings_cls: type[Settings] | None = None) -> Any:
    """Load one top-level settings field from the YAML layout + environment.

    Unlike :func:`get_settings`, invalid values in *other* sections do not
    make this fail — only ``name`` (plus its own state section) is
    validated. Raises :class:`ConfigError` naming the offending key, and the
    environment variable when an ``UNTAPED_*`` override supplied it.
    """
    _, loader = _section_models(settings_cls or get_settings_model(), name)
    try:
        return getattr(loader(), name)
    except ValidationError as exc:
        raise ConfigError(settings_error_message(exc)) from exc


class _EnvOverInit(BaseSettings):
    """Validate init data with ``UNTAPED_*`` env overrides layered on top."""

    model_config = SettingsConfigDict(
        env_prefix="UNTAPED_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (env_settings, init_settings)


def check_settings_field(name: str, node: Any, *, model: type[BaseModel] | None = None) -> Any:
    """Validate one top-level field from an effective YAML ``node`` plus env.

    ``model`` validates a capability section; without it ``name`` must be a
    core :class:`Settings` field (``log_level``/``http``/``ui``). ``node``
    ``None`` means the YAML does not set the field. Diagnostic helper for
    ``doctor``: raises :class:`ConfigError` via :func:`settings_error_message`
    (naming the env var when an override is the culprit).
    """
    if model is not None:
        definition: Any = (model, Field(default_factory=model))
    else:
        core = Settings.model_fields[name]
        definition = (core.annotation, core)
    checker = create_model("UntapedFieldCheck", __base__=_EnvOverInit, **{name: definition})
    try:
        return getattr(checker(**({} if node is None else {name: node})), name)
    except ValidationError as exc:
        raise ConfigError(settings_error_message(exc)) from exc


def settings_error_message(exc: ValidationError) -> str:
    """Describe a settings ``ValidationError``, naming an env var culprit."""
    detail = first_validation_error(exc)
    env_var = _env_culprit(exc)
    if env_var is not None:
        return f"invalid value in environment variable {env_var}: {detail}"
    path = resolve_config_path()
    errors = exc.errors()
    loc = errors[0].get("loc", ()) if errors else ()
    if loc and isinstance(loc[0], str):
        return f"invalid config section {loc[0]!r} in {path}: {detail}"
    return f"invalid config in {path}: {detail}"


def _env_culprit(exc: ValidationError) -> str | None:
    errors = exc.errors()
    if not errors:
        return None
    loc = [str(part) for part in errors[0].get("loc", ()) if isinstance(part, str)]
    # The deepest set ``UNTAPED_A__B__C`` wins; a JSON blob in ``UNTAPED_A``
    # can also supply a nested value.
    for depth in range(len(loc), 0, -1):
        candidate = "UNTAPED_" + "__".join(loc[:depth]).upper()
        if candidate in os.environ:
            return candidate
    return None


def get_core_settings() -> Settings:
    """Alias for callers that want to emphasize core-only settings access."""
    return get_settings()


def get_config_section[T: BaseModel](section: str, model_cls: type[T]) -> T:
    """Return one typed settings section, building a one-off model if needed.

    Only ``section`` (plus its own state section) is validated, so an invalid
    sibling section never breaks an unrelated capability.
    """
    settings_cls: type[Settings] | None = None
    if section not in _CONFIG_REGISTRY.profile_sections:
        settings_cls = _build_settings_model({section: model_cls}, _CONFIG_REGISTRY.state_sections)
    value = load_settings_section(section, settings_cls)
    if isinstance(value, model_cls):
        return value
    if isinstance(value, BaseModel):
        return model_cls.model_validate(value.model_dump())
    return model_cls.model_validate(value)


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


reset_config_registry_for_tests()
