"""Registry-backed configuration loaded from ``~/.untaped/config.yml``.

The unified composition root owns YAML/env loading and the in-process registry
of typed capability settings sections over the profiles layout. Capability
state lives in a separate ``state.yml`` (:func:`resolve_state_path`).
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from pydantic import BaseModel, Field, ValidationError, create_model
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)
from pydantic_settings.sources import EnvSettingsSource, InitSettingsSource

from untaped.deprecated_keys import KeyUse, key_mappings, rename_keys, use_warning, warn_once
from untaped.errors import ConfigError, first_validation_error
from untaped.settings_layout import ProfilesSettingsLayout, SectionModels
from untaped.theme import CONFIG_WRITE_CONTEXT, UiSettings

DEFAULT_CONFIG_PATH = "~/.untaped/config.yml"
STATE_FILE_NAME = "state.yml"
STATE_PATH_ENV = "UNTAPED_STATE"

#: On-disk format of ``config.yml`` and ``state.yml``. A file without
#: ``format_version`` is format 1. Bump only in a major release, when an older
#: reader ignoring a new core key would change behaviour (see CONTRIBUTING.md).
#: A renamed key (``renamed_keys``) does not bump it: a bump would make an
#: older release refuse the whole file, and ``untaped config migrate`` renames
#: keys only when the user runs it.
FORMAT_VERSION = 1


class HttpSettings(BaseModel):
    """Cross-cutting HTTP behaviour for a tool's HTTP client (per-profile)."""

    ca_bundle: Path | None = None
    verify_ssl: bool = True
    verify_hostname: bool = True
    timeout: float = Field(default=30.0, gt=0)
    proxy: str | None = None


class SkillsSettings(BaseModel):
    """What every run does about installed agent skills that are out of date (per-profile)."""

    updates: Literal["warn", "auto", "off"] = "warn"


class _ConfigRegistry:
    """Mutable in-process registry of the running tool's config sections."""

    def __init__(self) -> None:
        self.profile_sections: dict[str, type[BaseModel]] = {}
        self.state_sections: dict[str, type[BaseModel]] = {}

    def reset(self) -> None:
        self.profile_sections = {}
        self.state_sections = {}
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
            _RenamingEnvSource(settings_cls),
            LayoutSettingsSource(settings_cls, yaml_file=path),
            file_secret_settings,
        )


class Settings(_SettingsSources):
    """Base settings class; concrete aggregate models are built dynamically."""

    http: HttpSettings = Field(default_factory=HttpSettings)
    ui: UiSettings = Field(default_factory=UiSettings)
    skills: SkillsSettings = Field(default_factory=SkillsSettings)


def _model_sections(settings_cls: type[BaseModel]) -> dict[str, type[BaseModel]]:
    """``section -> model`` for every field of ``settings_cls`` holding a model."""
    return {
        name: field.annotation
        for name, field in settings_cls.model_fields.items()
        if isinstance(field.annotation, type) and issubclass(field.annotation, BaseModel)
    }


def _profile_section_models() -> SectionModels:
    return _model_sections(get_profile_settings_model())


_PROFILES_LAYOUT = ProfilesSettingsLayout(sections=_profile_section_models)


def active_settings_layout() -> ProfilesSettingsLayout:
    """Return the SDK's settings layout (the profiles layout, always)."""
    return _PROFILES_LAYOUT


def register_profile_settings(section: str, model: type[BaseModel]) -> None:
    """Register a tool's profile-scoped section (lives under ``profiles.<name>``)."""
    _CONFIG_REGISTRY.register_profile_settings(section, model)


def registered_profile_model(section: str) -> type[BaseModel] | None:
    """The profile model registered for ``section``, if any."""
    return _CONFIG_REGISTRY.profile_sections.get(section)


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

    ``http``/``ui``/``skills`` are base fields on :class:`Settings`;
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
        resolved = active_settings_layout().resolve(raw, sections=_model_sections(settings_cls))
        effective = resolved.effective
        for sections in resolved.uses.values():
            for section, uses in sections.items():
                for use in uses:
                    _warn_use(use, section=section)
        # Only splice (and so only validate) the state sections this model
        # actually declares: a broken state section must not block loading
        # an unrelated one (see :func:`load_settings_section`).
        splice_registered_state(effective, sections=settings_cls.model_fields)
        super().__init__(settings_cls, effective)


def _warn_use(use: KeyUse, *, section: str) -> None:
    """Warn once about an old key or deprecated setting read from ``config.yml``."""
    message = use_warning(
        use,
        old=f"{section}.{use.old}",
        new=f"{section}.{use.new}",
        kept=f"{section}.{use.kept}",
    )
    if message is not None:
        warn_once(message)


def _env_name(section: str, key: str) -> str:
    """The ``UNTAPED_*`` variable that sets ``key`` of ``section``."""
    return "UNTAPED_" + "__".join([section, *key.split(".")]).upper()


def _env_is_set(name: str) -> bool:
    return any(key.upper() == name for key in os.environ)


def _env_spelling(section: str, key: str) -> str:
    """How the environment spells ``key``: its variable when set, else a key in the JSON blob."""
    name = _env_name(section, key)
    return name if _env_is_set(name) else f"{key} in UNTAPED_{section.upper()}"


class _RenamingEnvSource(EnvSettingsSource):
    """The ``UNTAPED_*`` environment source, with old key names renamed.

    pydantic-settings builds each section's dict from its ``UNTAPED_<SECTION>__*``
    variables (and an ``UNTAPED_<SECTION>`` JSON blob) without checking the
    inner names, so old names arrive here and are renamed like YAML keys.
    """

    def __call__(self) -> dict[str, Any]:
        data = super().__call__()
        for section, model in _model_sections(self.settings_cls).items():
            value = data.get(section)
            if not isinstance(value, dict) or not key_mappings(model):
                continue
            data[section], uses = rename_keys(model, value)
            for use in uses:
                # The new key follows the old one's form: a variable or a blob key.
                as_variable = _env_is_set(_env_name(section, use.old))
                message = use_warning(
                    use,
                    old=_env_spelling(section, use.old),
                    new=_env_name(section, use.new) if as_variable else use.new,
                    kept=None if use.kept is None else _env_spelling(section, use.kept),
                )
                if message is not None:
                    warn_once(message)
        return data


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
    return _checked_root(raw, yaml_file)


def check_config_text(text: str, path: Path) -> None:
    """Raise :class:`ConfigError` when ``text`` is not a readable config document.

    Applies the same root and ``format_version`` checks as :func:`load_config_yaml`
    to text already read from ``path``.
    """
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"could not parse {path}: {exc}") from exc
    _checked_root(raw, path)


def _checked_root(raw: Any, path: Path) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(
            f"invalid config in {path}: the document root must be a mapping, "
            f"got {type(raw).__name__}"
        )
    _check_format(raw, path)
    return raw


class FormatVersionError(ConfigError):
    """A config or state file this release must not read or write."""


class NewerFormatError(FormatVersionError):
    """A file stamped with a ``format_version`` newer than :data:`FORMAT_VERSION`."""

    def __init__(self, message: str, *, version: int, path: Path) -> None:
        super().__init__(message)
        self.version = version
        self.path = path


def _check_format(raw: dict[str, Any], path: Path) -> None:
    if "format_version" not in raw:
        return
    value = raw["format_version"]
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise FormatVersionError(  # invalid stamps get the same write-path protection
            f"invalid format_version in {path}: expected a positive integer, got {value!r}"
        )
    if value > FORMAT_VERSION:
        raise NewerFormatError(
            f"{path} was written by a newer untaped (format {value}; "
            f"this release reads format {FORMAT_VERSION}); upgrade untaped",
            version=value,
            path=path,
        )


def splice_registered_state(
    effective: dict[str, Any], *, sections: Iterable[str] | None = None
) -> None:
    """Merge registered state sections from ``state.yml`` into an effective profile dict.

    ``sections`` limits the splice to the named sections (default: all);
    ``state.yml`` is only read when at least one registered state section is
    wanted, so a broken state file never blocks loading a settings-only
    section.
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
    for section, model in targets:
        state = state_raw.get(section)
        if not isinstance(state, dict):
            continue
        try:
            state_data = model.model_validate(state).model_dump(exclude_unset=True)
        except ValidationError as exc:
            raise ConfigError(
                f"invalid state section {section!r} in {state_path}: {first_validation_error(exc)}"
            ) from exc
        merged = effective.setdefault(section, {})
        if isinstance(merged, dict):
            merged.update(state_data)
        else:
            effective[section] = state_data


#: Top-level keys of ``config.yml`` and ``state.yml`` that core owns (the
#: profile layout and the on-disk format stamp), never usable as capability
#: state names.
RESERVED_STATE_SECTIONS = frozenset({"active", "profiles", "format_version"})


def check_state_section_name(section: str) -> None:
    """Reject a state section name that collides with ``config.yml``'s own keys."""
    if not section or section in RESERVED_STATE_SECTIONS or section in Settings.model_fields:
        raise ConfigError(f"reserved or invalid state section name: {section!r}")


def resolve_state_path() -> Path:
    """Return the active state file path.

    ``UNTAPED_STATE`` wins; otherwise the name derives from the resolved
    config file in the same directory: ``config.yml`` → ``state.yml``
    (``~/.untaped/state.yml`` by default), any other ``<stem>.<ext>`` →
    ``<stem>.state.yml``, so sibling config files never share state. The
    state file must never be the config file itself.
    """
    config_path = resolve_config_path()
    override = os.environ.get(STATE_PATH_ENV, "").strip()
    if override:
        path = Path(override).expanduser()
    elif config_path.name == "config.yml":
        path = config_path.parent / STATE_FILE_NAME
    else:
        path = config_path.parent / f"{config_path.stem}.{STATE_FILE_NAME}"
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
    settings_cls = get_settings_model()
    try:
        return settings_cls()
    except ValidationError as exc:
        raise ConfigError(settings_error_message(exc, settings_cls)) from exc


def validate_config_file(candidate: Path) -> None:
    """Validate ``candidate`` as if it were the config file, without loading it.

    Checks exactly what :func:`get_settings` would (environment overrides and
    capability state included) against the candidate's content instead of
    the active config file's. Raises :class:`ConfigError`.
    """

    class _Candidate(get_settings_model()):  # type: ignore[misc]
        @classmethod
        def settings_customise_sources(
            cls,
            settings_cls: type[BaseSettings],
            init_settings: PydanticBaseSettingsSource,
            env_settings: PydanticBaseSettingsSource,
            dotenv_settings: PydanticBaseSettingsSource,
            file_secret_settings: PydanticBaseSettingsSource,
        ) -> tuple[PydanticBaseSettingsSource, ...]:
            layout = LayoutSettingsSource(settings_cls, candidate)
            env = _RenamingEnvSource(settings_cls)
            return (init_settings, env, layout, file_secret_settings)

    try:
        _Candidate()
    except ValidationError as exc:
        raise ConfigError(settings_error_message(exc, _Candidate)) from exc


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
        raise ConfigError(settings_error_message(exc, loader)) from exc


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
        return (_RenamingEnvSource(settings_cls), init_settings)


def check_settings_field(name: str, node: Any, *, model: type[BaseModel] | None = None) -> Any:
    """Validate one top-level field from an effective YAML ``node`` plus env.

    ``model`` validates a capability section; without it ``name`` must be a
    core :class:`Settings` field (``http``/``ui``/``skills``). ``node``
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
        raise ConfigError(settings_error_message(exc, checker)) from exc


def settings_error_message(exc: ValidationError, settings_cls: type[BaseModel]) -> str:
    """Describe a settings ``ValidationError``, naming an env var culprit.

    An old-spelling variable of a renamed key of ``settings_cls`` is named
    too (``UNTAPED_GITHUB__CORPUS_PATH`` for ``github.cache_dir``).
    """
    detail = first_validation_error(exc)
    env_var = _env_culprit(exc, settings_cls)
    if env_var is not None:
        return f"invalid value in environment variable {env_var}: {detail}"
    path = resolve_config_path()
    errors = exc.errors()
    loc = errors[0].get("loc", ()) if errors else ()
    if loc and isinstance(loc[0], str):
        return f"invalid config section {loc[0]!r} in {path}: {detail}"
    return f"invalid config in {path}: {detail}"


def _env_culprit(exc: ValidationError, settings_cls: type[BaseModel]) -> str | None:
    errors = exc.errors()
    if not errors:
        return None
    loc = [str(part) for part in errors[0].get("loc", ()) if isinstance(part, str)]
    readable = _readable_old_keys(settings_cls, loc[0]) if loc else {}
    # The deepest set ``UNTAPED_A__B__C`` wins (or a variable spelling one of
    # its old names); a JSON blob in ``UNTAPED_A`` can also supply a nested value.
    for depth in range(len(loc), 0, -1):
        candidate = "UNTAPED_" + "__".join(loc[:depth]).upper()
        if candidate in os.environ:
            return candidate
        path = ".".join(loc[1:depth])
        for old in sorted(old for old, new in readable.items() if new == path):
            name = _env_name(loc[0], old)
            if _env_is_set(name):
                return name
    return None


def _readable_old_keys(settings_cls: type[BaseModel], section: str) -> dict[str, str]:
    model = _model_sections(settings_cls).get(section)
    return {} if model is None else dict(key_mappings(model).readable)


def get_config_section[T: BaseModel](section: str, model_cls: type[T]) -> T:
    """Return one typed settings section, building a one-off model if needed.

    Only ``section`` (plus its own state section) is validated, so an invalid
    sibling section never breaks an unrelated capability. A token-bearing
    section gets its token fallbacks applied (:func:`untaped.auth.resolve_token`).
    """
    settings_cls: type[Settings] | None = None
    if section not in _CONFIG_REGISTRY.profile_sections:
        settings_cls = _build_settings_model({section: model_cls}, _CONFIG_REGISTRY.state_sections)
    from untaped.auth import resolve_token  # noqa: PLC0415

    value = load_settings_section(section, settings_cls)
    if isinstance(value, model_cls):
        return resolve_token(value, section=section)
    if isinstance(value, BaseModel):
        return resolve_token(model_cls.model_validate(value.model_dump()), section=section)
    return resolve_token(model_cls.model_validate(value), section=section)


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
