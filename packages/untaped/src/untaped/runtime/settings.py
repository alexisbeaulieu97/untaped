"""The settings registry and the sources that resolve a plugin's section.

It keeps the in-process registry of typed plugin settings sections, the
settings sources (the profiles layout with the run's overlay, environment
variables over init values), and the loaders and validators of a section
and of core's parts. Reading ``config.yml`` lives in
:mod:`untaped.runtime.config_read`, the environment source in
:mod:`untaped.runtime.settings_env` and a section's models in
:mod:`untaped.runtime.section_models`. A plugin's state lives in its own
folder (:mod:`untaped.sdk.storage.state`).
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import replace
from functools import cache, lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal, cast, overload

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    create_model,
)
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)
from pydantic_settings.sources import InitSettingsSource

from untaped.runtime.config_read import load_config_yaml
from untaped.runtime.profile_resolver import (
    DEFAULT_PROFILE,
    FILE_KEYS,
    picked_names,
    settings_key,
)
from untaped.runtime.section_models import RESERVED_SECTION_KEYS, model_sections
from untaped.runtime.settings_env import RenamingEnvSource, env_is_set, env_name_of, env_var_name
from untaped.runtime.settings_layout import ProfilesSettingsLayout, ResolvedConfig, SectionModels
from untaped.runtime.theme import CONFIG_WRITE_CONTEXT, CONFIG_WRITTEN_KEY_CONTEXT, UiSettings
from untaped.sdk._home import PLUGINS_FOLDER, config_path, home
from untaped.sdk._run import current_overlay
from untaped.sdk.errors import ConfigError, UntapedError, first_validation_error
from untaped.sdk.health.deprecated_keys import (
    KeyUse,
    key_mappings,
    notify_use,
)
from untaped.sdk.marks import Stability
from untaped.sdk.words import run_hint


def plugins_root() -> Path:
    """The home's ``plugins/``: every plugin's folder, installed here or not."""
    return home().expanduser() / PLUGINS_FOLDER


class HttpSettings(BaseModel):
    """Cross-cutting HTTP behaviour for a plugin's HTTP client (per-profile)."""

    model_config = ConfigDict(frozen=True)

    ca_bundle_path: Path | None = Field(
        default=None,
        description="PEM file of extra CA certificates to trust instead of the OS trust store.",
    )
    timeout_seconds: float = Field(
        default=30.0, gt=0, description="HTTP request timeout in seconds."
    )
    proxy: str | None = Field(
        default=None,
        description=(
            "Proxy URL for HTTP clients, and for Git fetches over HTTPS. "
            "When unset, standard proxy variables (and, for Git, your Git config) "
            "apply."
        ),
    )


class SkillsSettings(BaseModel):
    """What every run does about agent skills an older untaped wrote (per-profile)."""

    model_config = ConfigDict(frozen=True)

    updates: Literal["warn", "auto", "off"] = Field(
        default="auto",
        description=(
            "What each run does about agent skills in your home folder that an "
            "older untaped wrote: `auto` (refresh them), `warn` (print a warning), "
            "or `off`. Skills inside a project are never changed by a run."
        ),
    )


class _ConfigRegistry:
    """Mutable in-process registry of the composed plugins' config sections."""

    def __init__(self) -> None:
        self.profile_sections: dict[str, type[BaseModel]] = {}
        self.section_stability: dict[str, Stability | None] = {}

    def reset(self) -> None:
        self.profile_sections = {}
        self.section_stability = {}
        get_settings.cache_clear()
        get_settings_model.cache_clear()

    def register_profile_settings(
        self, section: str, model: type[BaseModel], stability: Stability | None = None
    ) -> None:
        _reject_reserved_section(section)
        existing = self.profile_sections.get(section)
        if existing is not None and existing is not model:
            raise ConfigError(f"duplicate profile settings section: {section}")
        self.profile_sections[section] = model
        self.section_stability[section] = stability
        get_settings.cache_clear()
        get_settings_model.cache_clear()


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
            RenamingEnvSource(settings_cls),
            LayoutSettingsSource(settings_cls, yaml_file=path),
            file_secret_settings,
        )


class Settings(_SettingsSources):
    """Base settings class; concrete aggregate models are built dynamically."""


def profile_section_models() -> SectionModels:
    """``section -> model`` for every profile section: core's and each plugin's."""
    return model_sections(get_settings_model())


_PROFILES_LAYOUT = ProfilesSettingsLayout(sections=profile_section_models)


def active_settings_layout() -> ProfilesSettingsLayout:
    """Return the SDK's settings layout (the profiles layout, always)."""
    return _PROFILES_LAYOUT


def register_profile_settings(
    section: str, model: type[BaseModel], stability: Stability | None = None
) -> None:
    """Register a plugin's profile-scoped section (in the base and each ``profiles.<name>``).

    ``stability`` is the owning plugin's mark; its settings inherit it
    unless a field carries a mark of its own.
    """
    _CONFIG_REGISTRY.register_profile_settings(section, model, stability)


def section_stabilities() -> Mapping[str, Stability | None]:
    """Each registered profile section's plugin mark (``None`` for an unmarked one)."""
    return _CONFIG_REGISTRY.section_stability


def registered_profile_model(section: str) -> type[BaseModel] | None:
    """The settings model registered for ``section``, if any."""
    return _CONFIG_REGISTRY.profile_sections.get(section)


def _reject_reserved_section(section: str) -> None:
    """Reject a plugin section name that core owns (:data:`RESERVED_SECTIONS`).

    ``http``/``ui``/``skills`` are base fields on :class:`Settings`;
    registering a plugin section with one of those names would shadow the SDK
    field in the dynamically built model and break config resolution.
    """
    if section in RESERVED_SECTIONS:
        raise ConfigError(f"reserved SDK settings section: {section!r}")


def reset_config_registry() -> None:
    """Forget every registered config section and drop the settings caches.

    Composition registers the sections again (``bootstrap._reregister``);
    tests call it for isolation.
    """
    _CONFIG_REGISTRY.reset()


def apply_overlay(effective: dict[str, Any], *, profile: str) -> dict[str, Any]:
    """``effective`` with the run's overlay's section updated, when it is for ``profile``.

    Returns ``effective`` itself when the run has no overlay
    (:attr:`untaped.sdk._run.Run.overlay`) or it is for another profile;
    otherwise a copy (``effective`` is never mutated). A ``SecretStr`` is
    unwrapped because the layout feeds validation, which takes plain values.
    """
    overlay = current_overlay()
    if overlay is None or overlay.profile != profile:
        return effective
    node = effective.get(overlay.section)
    section = dict(node) if isinstance(node, dict) else {}
    for key, value in overlay.values.items():
        # A dotted key (``server.url``) is a leaf of a nested setting.
        *parents, leaf = key.split(".")
        target = section
        for part in parents:
            child = target.get(part)
            target[part] = dict(child) if isinstance(child, dict) else {}
            target = target[part]
        if value is None:
            target.pop(leaf, None)
        else:
            target[leaf] = value.get_secret_value() if isinstance(value, SecretStr) else value
    return {**effective, overlay.section: section}


def resolve_with_overlay(
    raw: dict[str, Any],
    *,
    sections: SectionModels | None = None,
) -> ResolvedConfig:
    """The layout's resolution of ``raw`` with the run's overlay laid over it.

    The overlay's profile may not exist in ``raw`` yet (``setup`` checks a new
    profile before creating it); it then resolves as the empty profile it will
    be, over the base.
    """
    overlay = current_overlay()
    profiles = raw.get("profiles")
    # A malformed ``profiles`` (not a mapping) is left for resolution to report.
    known = {} if profiles is None else profiles
    if (
        overlay is not None
        and overlay.profile != DEFAULT_PROFILE
        and isinstance(known, dict)
        and overlay.profile not in known
    ):
        raw = {**raw, "profiles": {**known, overlay.profile: {}}}
    resolved = active_settings_layout().resolve(raw, sections=sections)
    selected = (picked_names() or (DEFAULT_PROFILE,))[-1]  # the overlay is for the top layer
    return replace(resolved, effective=apply_overlay(resolved.effective, profile=selected))


class LayoutSettingsSource(InitSettingsSource):
    """Pydantic-settings source reading YAML through the active settings layout."""

    def __init__(self, settings_cls: type[BaseSettings], yaml_file: Path) -> None:
        raw = load_config_yaml(yaml_file)
        resolved = resolve_with_overlay(raw, sections=model_sections(settings_cls))
        effective = resolved.effective
        for sections in resolved.uses.values():
            for section, uses in sections.items():
                for use in uses:
                    notify_config_key(use, section=section)
        super().__init__(settings_cls, effective)


_MIGRATE_FIXES = {"renamed": "rename it in", "ignored": "remove it from"}


def notify_config_key(use: KeyUse, *, section: str) -> None:
    """Warn once about an old key or deprecated setting read from ``config.yml``.

    A renamed or ignored key's warning hints at ``config migrate``.
    """
    fix = _MIGRATE_FIXES.get(use.kind)
    notify_use(
        use,
        section=section,
        old=f"{section}.{use.old}",
        new=f"{section}.{use.new}",
        kept=f"{section}.{use.kept}",
        hint=None if fix is None else f"{run_hint('config migrate')} to {fix} config.yml",
    )


#: Config sections core owns: the top-level layout keys (``format_version``,
#: ``profiles``, 11.x's ``active``) and the keys injected into plugin sections
#: (:data:`RESERVED_SECTION_KEYS`). No plugin section may take one, so a plugin
#: named after one is quarantined (``duplicate-name``).
RESERVED_SECTIONS = frozenset({*RESERVED_SECTION_KEYS, "active", *FILE_KEYS})


def resolve_config_path() -> Path:
    """``config.yml`` at the home's root (:func:`untaped.sdk.home`)."""
    return config_path()


@lru_cache(maxsize=1)
def get_settings_model() -> type[Settings]:
    """Build the current aggregate settings model from registered sections."""
    return _build_settings_model(_CONFIG_REGISTRY.profile_sections)


def _build_settings() -> Settings:
    settings_cls = get_settings_model()
    try:
        return settings_cls()
    except ValidationError as exc:
        raise ConfigError(settings_error_message(exc, settings_cls)) from exc


def _build_picked(_key: tuple[object, ...]) -> Settings:
    """The aggregate settings of the current context, whose :func:`settings_key` is ``_key``."""
    return _build_settings()


class _SettingsGetter:
    """``get_settings``: the aggregate settings, cached per pick, uncached under a run's overlay.

    The cache is keyed by the profiles the current run picks and its project's
    file (:func:`settings_key`), so runs that pick differently (a child run, a
    thread in one, an asyncio task in another) or run in another project each
    read their own entry and none ever clears another's. Under
    a run carrying an overlay the settings are built fresh and never stored, so
    candidate values reach no cache. The key is the pick alone, not the home or
    the environment: a config write, registration or reset drops every pick's
    entry through ``cache_clear`` (``cache_info`` is the wrapped ``lru_cache``'s).
    """

    def __init__(self) -> None:
        self._cached = lru_cache(maxsize=16)(_build_picked)

    def __call__(self) -> Settings:
        """Return the aggregate settings of the current pick, cached."""
        if current_overlay() is not None:
            return _build_settings()
        return self._cached(settings_key())

    def cache_clear(self) -> None:
        """Drop the cached settings of every pick."""
        self._cached.cache_clear()

    def cache_info(self) -> Any:
        """The cache's hit and size counters (``functools.lru_cache``'s)."""
        return self._cached.cache_info()


get_settings = _SettingsGetter()


def validate_config_file(candidate: Path) -> None:
    """Validate ``candidate`` as if it were the config file, without loading it.

    Checks exactly what :func:`get_settings` would (environment overrides
    included) against the candidate's content instead of
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
            env = RenamingEnvSource(settings_cls)
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
    data: Mapping[str, Any],
    name: str,
    settings_cls: type[Settings] | None = None,
    *,
    written_key: str | None = None,
) -> Any:
    """Validate only the top-level field ``name`` of ``data`` (no disk/env reads).

    A missing key validates the field's default (so a required field that is
    absent is reported). Write-time checks (``CONFIG_WRITE_CONTEXT``) run
    too, e.g. an unknown ``untaped.ui.theme`` is rejected. ``written_key`` (the full
    key being written) limits the per-key declared-name checks to that key, so
    a stray value in another key does not block the write. Raises
    :class:`pydantic.ValidationError`; error locations start with ``name``.
    """
    validator, _ = _section_models(settings_cls or get_settings_model(), name)
    payload = {name: data[name]} if name in data else {}
    validated = validator.model_validate(
        payload,
        context={CONFIG_WRITE_CONTEXT: True, CONFIG_WRITTEN_KEY_CONTEXT: written_key},
    )
    return getattr(validated, name)


#: The parts of core's section, each validated alone (:func:`load_core_part`).
CorePart = Literal["http", "ui", "skills"]


#: Core's section: ``untaped.core.settings.NAME``, which imports this module.
_CORE = "untaped"


def only_part(model: type[BaseModel], part: str) -> Any:
    """``model``, validating only its field ``part``: the others are dropped and default.

    Sources still see ``model`` itself, so its renamed keys apply. Use it as
    a field's type, or pass it to :func:`check_settings_field`.
    """

    def _keep(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {part: value[part]} if part in value else {}
        return value

    return Annotated[model, BeforeValidator(_keep)]


@cache
def _core_part_models(part: CorePart) -> tuple[type[BaseModel], type[_SettingsSources]]:
    """A plain validator and an env/YAML loader of core's section, reading ``part`` alone."""
    from untaped.core.settings import NAME, CoreSettings  # noqa: PLC0415 - core imports this module

    definition: Any = (only_part(CoreSettings, part), Field(default_factory=CoreSettings))
    validator = create_model("UntapedCorePartValidator", **{NAME: definition})
    loader = create_model("UntapedCorePart", __base__=_SettingsSources, **{NAME: definition})
    return validator, loader


@overload
def load_core_part(part: Literal["http"]) -> HttpSettings: ...


@overload
def load_core_part(part: Literal["ui"]) -> UiSettings: ...


@overload
def load_core_part(part: Literal["skills"]) -> SkillsSettings: ...


def load_core_part(part: CorePart) -> BaseModel:
    """One part of core's section (``untaped.http``, ``.ui`` or ``.skills``), validated alone.

    A bad value in one part never breaks a reader of another: a ``ui`` typo
    leaves every HTTP client working. Raises :class:`ConfigError` as
    :func:`load_settings_section` does.
    """
    _, loader = _core_part_models(part)
    try:
        return cast("BaseModel", getattr(getattr(loader(), _CORE), part))
    except ValidationError as exc:
        raise ConfigError(settings_error_message(exc, loader)) from exc


def validate_core_part(
    data: Mapping[str, Any], part: CorePart, *, written_key: str | None = None
) -> BaseModel:
    """:func:`validate_settings_section` for one part of core's section, its siblings unread.

    A ``config set untaped.http.proxy`` is never refused for a bad ``untaped.ui`` value.
    """
    validator, _ = _core_part_models(part)
    payload = {_CORE: data[_CORE]} if _CORE in data else {}
    validated = validator.model_validate(
        payload,
        context={CONFIG_WRITE_CONTEXT: True, CONFIG_WRITTEN_KEY_CONTEXT: written_key},
    )
    return cast("BaseModel", getattr(getattr(validated, _CORE), part))


def load_settings_section(name: str, settings_cls: type[Settings] | None = None) -> Any:
    """Load one top-level settings field from the YAML layout + environment.

    Unlike :func:`get_settings`, invalid values in *other* sections do not
    make this fail — only ``name`` is validated. Raises :class:`ConfigError`
    naming the offending key, and the environment variable when an
    ``UNTAPED_*`` override supplied it.
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
        return (RenamingEnvSource(settings_cls), init_settings)


def check_settings_field(
    name: str, node: Any, *, model: type[BaseModel], part: str | None = None
) -> Any:
    """Validate the section ``name`` (``model``) from an effective YAML ``node`` plus env.

    ``part`` validates only that field of it (:func:`only_part`). ``node``
    ``None`` means the YAML does not set the section. Diagnostic
    helper for ``doctor``: raises :class:`ConfigError` via
    :func:`settings_error_message` (naming the env var when an override is the
    culprit).
    """
    checked = model if part is None else only_part(model, part)
    definition: Any = (checked, Field(default_factory=model))
    checker = create_model("UntapedFieldCheck", __base__=_EnvOverInit, **{name: definition})
    try:
        return getattr(checker(**({} if node is None else {name: node})), name)
    except ValidationError as exc:
        raise ConfigError(settings_error_message(exc, checker)) from exc


def settings_error_message(exc: ValidationError, settings_cls: type[BaseModel]) -> str:
    """Describe a settings ``ValidationError``, naming an env var culprit.

    An old-spelling variable of a renamed key of ``settings_cls`` is named
    too (``UNTAPED_DEMO__OLD_KEY`` for ``demo.new_key``).
    """
    detail = first_validation_error(exc)
    env_var = _env_culprit(exc, settings_cls)
    if env_var is not None:
        return f"invalid value in environment variable {env_var}: {detail}"
    errors = exc.errors()
    loc = errors[0].get("loc", ()) if errors else ()
    path = _file_of([part for part in loc if isinstance(part, str)])
    if loc and isinstance(loc[0], str):
        return f"invalid config section {loc[0]!r} in {path}: {detail}"
    return f"invalid config in {path}: {detail}"


def _file_of(loc: Sequence[str]) -> Path:
    """The config file the value at ``loc`` came from: the project's when it set it."""
    from untaped.runtime.config_file import read_config_dict  # noqa: PLC0415
    from untaped.runtime.folder_layer import folder_layer  # noqa: PLC0415 - imports this module
    from untaped.sdk.network.credentials import FOLDER_SOURCE_PREFIX  # noqa: PLC0415

    home = resolve_config_path()
    try:
        layer = folder_layer()
        if layer is None or not loc:
            return home
        provenance = active_settings_layout().resolve(read_config_dict()).provenance
    except UntapedError, OSError:
        return home
    for depth in range(len(loc), 0, -1):
        source = provenance.get(tuple(loc[:depth]))
        if source is not None:
            return layer.file if str(source).startswith(FOLDER_SOURCE_PREFIX) else home
    return home


def _env_culprit(exc: ValidationError, settings_cls: type[BaseModel]) -> str | None:
    errors = exc.errors()
    if not errors:
        return None
    loc = [str(part) for part in errors[0].get("loc", ()) if isinstance(part, str)]
    readable = _readable_old_keys(settings_cls, loc[0]) if loc else {}
    # The deepest set ``UNTAPED_A__B__C`` wins (or a variable spelling one of
    # its old names); a JSON blob in ``UNTAPED_A`` can also supply a nested value.
    for depth in range(len(loc), 0, -1):
        candidate = env_var_name(loc[:depth])
        if candidate in os.environ:
            return candidate
        path = ".".join(loc[1:depth])
        for old in sorted(old for old, new in readable.items() if new == path):
            name = env_name_of(loc[0], old)
            if env_is_set(name):
                return name
    return None


def _readable_old_keys(settings_cls: type[BaseModel], section: str) -> dict[str, str]:
    model = model_sections(settings_cls).get(section)
    return {} if model is None else dict(key_mappings(model).readable)


def get_config_section[T: BaseModel](section: str, model_cls: type[T]) -> T:
    """Return one typed settings section, building a one-off model if needed.

    Only ``section`` is validated, so an invalid sibling section never breaks
    an unrelated plugin.
    """
    settings_cls: type[Settings] | None = None
    if section not in _CONFIG_REGISTRY.profile_sections:
        settings_cls = _build_settings_model({section: model_cls})
    value = load_settings_section(section, settings_cls)
    if isinstance(value, model_cls):
        return value
    if isinstance(value, BaseModel):
        return model_cls.model_validate(value.model_dump())
    return model_cls.model_validate(value)


def _build_settings_model(profile_sections: Mapping[str, type[BaseModel]]) -> type[Settings]:
    from untaped.core.settings import NAME, CoreSettings  # noqa: PLC0415 - core imports this module

    # Core's section is there before composition registers it too (as a plugin's),
    # so a model built before composition (or for a test's own sections) has it.
    profile_sections = {NAME: CoreSettings, **profile_sections}
    fields: dict[str, Any] = {
        section: (model, Field(default_factory=model))
        for section, model in profile_sections.items()
    }
    return cast(
        "type[Settings]",
        create_model("UntapedSettings", __base__=Settings, **fields),
    )


reset_config_registry()
