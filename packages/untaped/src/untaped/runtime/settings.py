"""Registry-backed configuration loaded from the home's ``config.yml``.

The unified composition root owns YAML/env loading and the in-process registry
of typed plugin settings sections over the profiles layout. Plugin
state lives in a separate ``state.yml`` (:func:`resolve_state_path`).
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import os
import re
import weakref
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, replace
from functools import cache, lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, Literal, cast, overload

import yaml
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    ValidationError,
    create_model,
)
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)
from pydantic_settings.sources import EnvSettingsSource, InitSettingsSource

from untaped.runtime.profile_resolver import (
    DEFAULT_PROFILE,
    FILE_KEYS,
    picked_names,
)
from untaped.runtime.settings_layout import ProfilesSettingsLayout, ResolvedConfig, SectionModels
from untaped.runtime.theme import CONFIG_WRITE_CONTEXT, CONFIG_WRITTEN_KEY_CONTEXT, UiSettings
from untaped.sdk._home import config_path, home
from untaped.sdk.errors import ConfigError, first_validation_error
from untaped.sdk.health.deprecated_keys import (
    KeyUse,
    Renamed,
    key_mappings,
    notify_use,
    rename_keys,
)
from untaped.sdk.marks import Stability
from untaped.sdk.words import run_hint

if TYPE_CHECKING:
    from untaped.sdk.network.credentials import Credential
    from untaped.sdk.storage.cache import Cache

STATE_FILE_NAME = "state.yml"


def plugins_root() -> Path:
    """The home's ``plugins/``: every plugin's folder, installed here or not."""
    return home().expanduser() / "plugins"


#: On-disk format of ``config.yml``: format 2 holds the base at the top level
#: and named profiles under ``profiles:``. A non-empty file must carry
#: ``format_version: 2``; an unstamped one is 11.x's format 1, refused. Bump
#: only in a major release, when an older reader ignoring a new core key would
#: change behaviour (see CONTRIBUTING.md). A renamed key (``renamed_keys``)
#: does not bump it: a bump would make an older release refuse the whole file,
#: and ``untaped config migrate`` renames keys only when the user runs it.
FORMAT_VERSION = 2
#: On-disk format of the top-level ``state.yml``, unstamped until it leaves
#: the top of the home.
STATE_FORMAT_VERSION = 1


class HttpSettings(BaseModel):
    """Cross-cutting HTTP behaviour for a plugin's HTTP client (per-profile)."""

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
            "Proxy URL for HTTP clients, and for Git fetches from the GitHub host. "
            "When unset, standard proxy variables (and, for Git, your Git config) "
            "apply."
        ),
    )


class SkillsSettings(BaseModel):
    """What every run does about agent skills an older untaped wrote (per-profile)."""

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
        self.state_sections: dict[str, type[BaseModel]] = {}
        self.section_stability: dict[str, Stability | None] = {}

    def reset(self) -> None:
        self.profile_sections = {}
        self.state_sections = {}
        self.section_stability = {}
        get_settings.cache_clear()
        get_settings_model.cache_clear()
        get_profile_settings_model.cache_clear()

    def register_profile_settings(
        self, section: str, model: type[BaseModel], stability: Stability | None = None
    ) -> None:
        _reject_reserved_section(section)
        existing = self.profile_sections.get(section)
        if existing is not None and existing is not model:
            raise ConfigError(f"duplicate profile settings section: {section}")
        state_model = self.state_sections.get(section)
        if state_model is not None:
            validate_disjoint_settings_sections(section, model, state_model)
        self.profile_sections[section] = model
        self.section_stability[section] = stability
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


#: A contract's or a contract method's name in settings: snake_case.
ContractName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")]

#: The grammar every plugin name follows.
PLUGIN_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")

#: A plugin's name in settings, checked against the grammar only: a ranking
#: in the base must not break a profile without that plugin.
PluginName = Annotated[str, StringConstraints(pattern=PLUGIN_NAME_PATTERN.pattern)]


def _distinct(names: list[str]) -> list[str]:
    repeated = sorted({name for name in names if names.count(name) > 1})
    if repeated:
        raise ValueError(f"{', '.join(repeated)} ranked twice")
    return names


class ExtensionSettings(BaseModel):
    """One contract's settings under ``<owner>.extensions.<contract>``.

    ``rank`` orders the providers of each method, first first; a provider
    it doesn't name ranks below every one it does.
    """

    model_config = ConfigDict(extra="forbid")

    rank: dict[ContractName, Annotated[list[PluginName], AfterValidator(_distinct)]] = Field(
        default_factory=dict
    )


#: Keys the SDK injects into a plugin's section, so its own settings and
#: state models may not declare them: ``extensions`` on every contract owner,
#: ``caches`` for the caches a plugin declares.
RESERVED_SECTION_KEYS = frozenset({"extensions", "caches"})


def reserved_section_keys(
    model: type[BaseModel], *, injected: frozenset[str] = frozenset()
) -> list[str]:
    """The keys of ``model`` that collide with a key the SDK injects.

    No field and no field alias may take a :data:`RESERVED_SECTION_KEYS`
    name. An old key (``renamed_keys``, ``retired_keys``) collides only with
    a key the SDK actually ``injected`` into this section, so a plugin can
    still move its own ``extensions`` setting away under a new name.

    A credential's deprecated ``<name>``/``<name>_command`` settings are
    injected unless the model declares them, so they never collide.
    """
    keys = set(model.model_fields)
    for field in model.model_fields.values():
        keys.update(
            alias for alias in (field.alias, field.validation_alias) if isinstance(alias, str)
        )
    taken = RESERVED_SECTION_KEYS & keys
    for name in ("renamed_keys", "retired_keys"):
        declared = getattr(model, name, None)
        if isinstance(declared, Mapping):
            taken |= injected & {str(old).partition(".")[0] for old in declared}
    return sorted(taken)


class CacheSettings(BaseModel):
    """One cache's settings under ``<plugin>.caches.<cache>``; each cache's model subclasses it.

    An unknown key here loads, as anywhere in a plugin's section: doctor's
    unknown-keys row names it, and it never breaks the plugin's commands.
    """

    model_config = ConfigDict(extra="ignore")


@cache
def section_model(
    model: type[BaseModel],
    *,
    extensions: bool,
    caches: tuple[Cache[Any], ...],
    section: str = "",
    credentials: tuple[Credential[Any], ...] = (),
) -> type[BaseModel]:
    """``model`` with the keys the SDK injects into a plugin's section.

    ``extensions`` (every contract owner's) is typed structurally (contract
    name → :class:`ExtensionSettings`), so reading settings never imports an
    owner's contracts: whether a contract or method exists is for ``gather``
    and doctor to say, never a load error. ``caches`` holds one
    ``max_age_seconds`` per declared cache, defaulting to its declared max
    age; an unknown cache name loads (doctor names it), so a cache a plugin
    drops never breaks a config. Each cache's ``renamed_from`` joins
    ``model``'s ``renamed_keys`` here, on the composed class only: the
    owner's own declarations never point at an injected key.
    """
    caches = timed_caches(caches)
    renamed = _cache_renames(model, caches)
    if renamed:
        model = cast(
            "type[BaseModel]",
            type(
                model.__name__,
                (model,),
                {
                    "__module__": model.__module__,
                    "__qualname__": model.__qualname__,
                    "__annotations__": {"renamed_keys": ClassVar[Mapping[str, str | Renamed]]},
                    "renamed_keys": MappingProxyType(renamed),
                },
            ),
        )
    injected: dict[str, Any] = {}
    if extensions:
        injected["extensions"] = (
            dict[ContractName, ExtensionSettings],
            Field(default_factory=dict, description="Settings of the contracts this plugin owns."),
        )
    if caches:
        held = _caches_model(model, caches)
        injected["caches"] = (
            held,
            Field(default_factory=held, description="Max ages of the caches this plugin keeps."),
        )
    composed = cast(
        "type[BaseModel]",
        create_model(model.__name__, __base__=model, __module__=model.__module__, **injected),
    )
    if credentials:
        _CREDENTIAL_NAMES[composed] = frozenset(each.name for each in credentials)
    return composed


#: Each composed section model's credential names: ``UNTAPED_<SECTION>__<NAME>`` overrides one.
_CREDENTIAL_NAMES: weakref.WeakKeyDictionary[type[BaseModel], frozenset[str]] = (
    weakref.WeakKeyDictionary()
)


def _cache_renames(
    model: type[BaseModel], caches: tuple[Cache[Any], ...]
) -> dict[str, str | Renamed]:
    """``model``'s ``renamed_keys`` plus each cache's ``renamed_from``; ``{}`` when none renames.

    A cache's old keys are dated by its ``removed_in`` (undated without one).

    Raises :class:`ConfigError` for an old key two caches claim, or one the
    owner already declares (renamed or retired).
    """
    claimed: dict[str, Cache[Any]] = {}
    for declared in caches:
        for old in declared.renamed_from:
            if old in claimed:
                raise ConfigError(
                    f"old key {old!r} is renamed by both caches "
                    f"{claimed[old].name!r} and {declared.name!r}"
                )
            claimed[old] = declared
    if not claimed:
        return {}
    own: dict[str, Any] = {}
    for name in ("renamed_keys", "retired_keys"):
        mapping = getattr(model, name, None)
        if isinstance(mapping, Mapping):
            if clash := sorted(mapping.keys() & claimed.keys()):
                raise ConfigError(
                    f"old key {clash[0]!r} is in {model.__name__}.{name} and in the cache "
                    f"{claimed[clash[0]].name!r}'s renamed_from"
                )
            if name == "renamed_keys":
                own = dict(mapping)
    return own | {
        old: Renamed(f"caches.{declared.name}.max_age_seconds", removed_in=declared.removed_in)
        for old, declared in claimed.items()
    }


def timed_caches(caches: Iterable[Cache[Any]]) -> tuple[Cache[Any], ...]:
    """The caches with a max age: each has a ``caches.<name>.max_age_seconds`` setting.

    A version-only cache (``max_age=None``) has none.
    """
    return tuple(cache for cache in caches if not cache.version_only)


def _caches_model(model: type[BaseModel], caches: tuple[Cache[Any], ...]) -> type[BaseModel]:
    """The ``caches`` key's model: one :class:`CacheSettings` field per cache."""
    fields: dict[str, Any] = {}
    for declared in caches:
        max_age = declared.default_max_age
        assert max_age is not None  # timed_caches(): only a cache with a max age has a setting
        seconds: Any = (
            int,
            Field(
                default=int(max_age.total_seconds()),
                ge=0,
                description=(
                    f"Seconds {declared.label} is served before untaped checks upstream; "
                    "0 checks every time."
                ),
            ),
        )
        held = create_model(
            "".join(part.title() for part in declared.name.split("_")) + "CacheSettings",
            __base__=CacheSettings,
            __module__=model.__module__,
            max_age_seconds=seconds,
        )
        fields[declared.name] = (held, Field(default_factory=held))
    return cast(
        "type[BaseModel]",
        create_model(
            f"{model.__name__}Caches",
            __config__=ConfigDict(extra="ignore"),
            __module__=model.__module__,
            **fields,
        ),
    )


def model_sections(settings_cls: type[BaseModel]) -> dict[str, type[BaseModel]]:
    """``section -> model`` for every field of ``settings_cls`` holding a model."""
    return {
        name: field.annotation
        for name, field in settings_cls.model_fields.items()
        if isinstance(field.annotation, type) and issubclass(field.annotation, BaseModel)
    }


def profile_section_models() -> SectionModels:
    """``section -> model`` for every profile section: core's and each plugin's."""
    return model_sections(get_profile_settings_model())


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


def register_state_settings(section: str, model: type[BaseModel]) -> None:
    """Register a plugin's top-level state section spliced into the effective config."""
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


@dataclass(frozen=True)
class SettingsOverlay:
    """Candidate values for one section of one profile, layered over the loaded config.

    A ``None`` value means "unset this key" (a command token source removes the
    ``token``). Internal: ``setup`` checks a plugin against what the user
    typed before anything is written.
    """

    profile: str
    section: str
    values: Mapping[str, object]


_overlay: contextvars.ContextVar[SettingsOverlay | None] = contextvars.ContextVar(
    "untaped_settings_overlay", default=None
)


@contextlib.contextmanager
def settings_overlay(
    profile: str, section: str, values: Mapping[str, object]
) -> Iterator[SettingsOverlay]:
    """Read ``values`` as ``profile``'s ``section`` inside the block, in this context only.

    Both seams read it: the settings loader (:class:`LayoutSettingsSource`,
    which every section load goes through) and :func:`get_settings`, which
    skips its cache while an overlay is set, so candidate values are never
    cached and the main thread never sees them. A thread sees it only when its
    context was copied inside the block (the screen runtime's commands are).
    Nothing is written: no config file, no keychain.
    """
    overlay = SettingsOverlay(profile, section, dict(values))
    token = _overlay.set(overlay)
    try:
        yield overlay
    finally:
        _overlay.reset(token)


def active_overlay() -> SettingsOverlay | None:
    """The overlay set in this context, if any."""
    return _overlay.get()


def apply_overlay(effective: dict[str, Any], *, profile: str) -> dict[str, Any]:
    """``effective`` with the overlay's section updated, when it is for ``profile``.

    Returns ``effective`` itself when there is no overlay or it is for another
    profile; otherwise a copy (``effective`` is never mutated). A ``SecretStr``
    is unwrapped because the layout feeds validation, which takes plain values.
    """
    overlay = _overlay.get()
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
    """The layout's resolution of ``raw`` with the active overlay laid over it.

    The overlay's profile may not exist in ``raw`` yet (``setup`` checks a new
    profile before creating it); it then resolves as the empty profile it will
    be, over the base.
    """
    overlay = _overlay.get()
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
        # Only splice (and so only validate) the state sections this model
        # actually declares: a broken state section must not block loading
        # an unrelated one (see :func:`load_settings_section`).
        splice_registered_state(effective, sections=settings_cls.model_fields)
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


def env_var_name(path: Iterable[str]) -> str:
    """The ``UNTAPED_*`` variable that sets the setting at ``path`` (``("github", "token")``).

    The one place an environment name is built. A hyphen becomes an
    underscore, as in the plugin's import package (``acme-tools`` reads
    ``UNTAPED_ACME_TOOLS__KEY``): plugin names hold no ``_``, so the
    spelling stays unambiguous, and ``__`` stays the nesting delimiter.
    """
    return "UNTAPED_" + "__".join(part.replace("-", "_") for part in path).upper()


def _env_name(section: str, key: str) -> str:
    """The ``UNTAPED_*`` variable that sets ``key`` of ``section``."""
    return env_var_name([section, *key.split(".")])


def _env_is_set(name: str) -> bool:
    return any(key.upper() == name for key in os.environ)


def env_sets_key(section: str, key: str) -> bool:
    """Whether the environment sets top-level field ``key`` of ``section``, blank or not.

    Its ``UNTAPED_<SECTION>__<KEY>`` variable, or the key inside an
    ``UNTAPED_<SECTION>`` JSON object; a blob that isn't a JSON object
    counts, since it cannot be told apart. Names match case-insensitively,
    as the settings source reads them. ``key`` is the current name; an old
    name from ``renamed_keys`` is not checked.
    """
    if _env_is_set(_env_name(section, key)):
        return True
    blob_name = env_var_name([section])
    blob = next((value for name, value in os.environ.items() if name.upper() == blob_name), None)
    if blob is None:
        return False
    try:
        data = json.loads(blob)
    except ValueError:
        return True
    return not isinstance(data, dict) or key in data


def env_var_setting(
    path: tuple[str, ...], *, environ: Mapping[str, str] | None = None
) -> str | None:
    """The set ``UNTAPED_*`` variable that supplies the leaf at ``path``, if any.

    The deepest one wins: the leaf's own variable, else an ancestor's JSON
    object that holds it; an ancestor whose value (or a level inside it)
    isn't a JSON object counts, since it cannot be told apart. Old spellings
    are not checked; ``env_var_supplying`` finds a field's. ``environ``
    defaults to this process's variables.
    """
    variables = os.environ if environ is None else environ
    for depth in range(len(path), 0, -1):
        name = env_var_name(path[:depth])
        value = next((text for key, text in variables.items() if key.upper() == name), None)
        if value is None:
            continue
        if depth == len(path):
            return name
        try:
            data: Any = json.loads(value)
        except ValueError:
            return name
        for part in path[depth:]:
            if not isinstance(data, dict):
                return name
            if part not in data:
                break
            data = data[part]
        else:
            return name
    return None


@dataclass(frozen=True)
class RetiredVariable:
    """A set ``UNTAPED_*`` variable naming a key that is no longer read."""

    name: str
    new: str | None = None
    """The current key's variable, for a retired key."""
    note: str | None = None
    """The :class:`~untaped.sdk.health.deprecated_keys.Retired` note, for a deleted key."""


def retired_env_vars(sections: Mapping[str, type[BaseModel]]) -> list[RetiredVariable]:
    """Each set variable spelling a retired or deleted key of ``sections``.

    Neither is read any more, so without this a variable left in a shell
    profile or CI is ignored with no signal.
    """
    found: list[RetiredVariable] = []
    for section, model in sections.items():
        mappings = key_mappings(model)
        for old in sorted(mappings.retired):
            name = _env_name(section, old)
            if _env_is_set(name):
                found.append(
                    RetiredVariable(name, new=_env_name(section, mappings.migratable[old]))
                )
        for old, deleted in sorted(mappings.deleted.items()):
            name = _env_name(section, old)
            if _env_is_set(name):
                found.append(RetiredVariable(name, note=deleted.reason()))
    return found


def _env_spelling(section: str, key: str) -> str:
    """How the environment spells ``key``: its variable when set, else a key in the JSON blob."""
    name = _env_name(section, key)
    return name if _env_is_set(name) else f"{key} in {env_var_name([section])}"


class _RenamingEnvSource(EnvSettingsSource):
    """The ``UNTAPED_*`` environment source, with old key names renamed.

    pydantic-settings builds each section's dict from its ``UNTAPED_<SECTION>__*``
    variables (and an ``UNTAPED_<SECTION>`` JSON blob) without checking the
    inner names, so old names arrive here and are renamed like YAML keys.
    """

    def _load_env_vars(self) -> Mapping[str, str | None]:
        """The environment, with a hyphenated section's variables under its field name.

        pydantic-settings looks a section up by its field name, so
        ``UNTAPED_ACME_TOOLS__KEY`` (see :func:`env_var_name`) is read as
        ``UNTAPED_ACME-TOOLS__KEY``.
        """
        env_vars = dict(super()._load_env_vars())
        for section in self.settings_cls.model_fields:
            if "-" not in section:
                continue
            spelled = env_var_name([section])
            field = f"{self.env_prefix}{section}"
            if not self.case_sensitive:
                spelled, field = spelled.lower(), field.lower()
            moved = [key for key in env_vars if key.partition("__")[0] == spelled]
            for key in moved:
                env_vars[field + key[len(spelled) :]] = env_vars.pop(key)
        return env_vars

    def __call__(self) -> dict[str, Any]:
        data = super().__call__()
        for section, model in model_sections(self.settings_cls).items():
            value = data.get(section)
            if not isinstance(value, dict) or not key_mappings(model):
                continue
            data[section], uses = rename_keys(model, value)
            overrides = _CREDENTIAL_NAMES.get(model, frozenset())
            for use in uses:
                if use.kind == "deprecated" and use.old in overrides:
                    continue  # UNTAPED_<SECTION>__<NAME> is a credential's override, not deprecated
                # The new key follows the old one's form: a variable or a blob key.
                as_variable = _env_is_set(_env_name(section, use.old))
                notify_use(
                    use,
                    section=section,
                    old=_env_spelling(section, use.old),
                    new=_env_name(section, use.new) if as_variable else use.new,
                    kept=None if use.kept is None else _env_spelling(section, use.kept),
                )
        return data


def load_config_yaml(yaml_file: Path) -> dict[str, Any]:
    """Parse ``config.yml`` into a dict (``{}`` when absent or empty).

    Unreadable files, YAML syntax errors, a non-mapping document root and a
    file that is not format 2 (:func:`check_config_format`) all raise
    :class:`ConfigError` naming the path.
    """
    raw = _read_yaml(yaml_file)
    check_config_format(raw, yaml_file)
    return raw


def load_state_yaml(yaml_file: Path) -> dict[str, Any]:
    """Parse the top-level ``state.yml`` into a dict (``{}`` when absent or empty).

    Raises as :func:`load_config_yaml` does, except that the file may be
    unstamped (:data:`STATE_FORMAT_VERSION`).
    """
    raw = _read_yaml(yaml_file)
    _check_stamp(raw, yaml_file, newest=STATE_FORMAT_VERSION)
    return raw


def _read_yaml(yaml_file: Path) -> dict[str, Any]:
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

    Applies the same root and format checks as :func:`load_config_yaml`
    to text already read from ``path``.
    """
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"could not parse {path}: {exc}") from exc
    check_config_format(_checked_root(raw, path), path)


def _checked_root(raw: Any, path: Path) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(
            f"invalid config in {path}: the document root must be a mapping, "
            f"got {type(raw).__name__}"
        )
    return raw


class FormatVersionError(ConfigError):
    """A config or state file this release must not read or write."""


class NewerFormatError(FormatVersionError):
    """A file stamped with a ``format_version`` newer than this release reads."""

    def __init__(self, message: str, *, version: int, path: Path) -> None:
        super().__init__(message)
        self.version = version
        self.path = path


class OlderFormatError(FormatVersionError):
    """A ``config.yml`` an untaped 11.x wrote (format 1): 12.0 has no upgrade path."""


def check_config_format(raw: dict[str, Any], path: Path) -> None:
    """Refuse a ``config.yml`` this release must not read: not format 2, or holding 11.x keys.

    11.x's keys are ``active:`` and ``profiles.default``. An empty file is a
    fresh home. Raises :class:`FormatVersionError` (exit 4), so nothing is
    written beside a file this release must not read.
    """
    if not raw:
        return
    stamp = raw.get("format_version")
    if "format_version" not in raw or (type(stamp) is int and stamp == 1):
        raise OlderFormatError(
            f"{path} was written by untaped 11.x (format 1); this release reads format "
            f"{FORMAT_VERSION} and has no upgrade path",
            hint=(
                f"delete it and run `untaped setup`, or keep untaped 11.1 for this home; "
                f"a file written for 12.0 starts with `format_version: {FORMAT_VERSION}`"
            ),
        )
    _check_stamp(raw, path, newest=FORMAT_VERSION)
    if "active" in raw:
        raise FormatVersionError(
            f"`active:` in {path} is not read: a run picks its profile with --profile "
            "or UNTAPED_PROFILE, else reads the base",
            hint="delete the `active:` line",
        )
    profiles = raw.get("profiles")
    if isinstance(profiles, dict) and DEFAULT_PROFILE in profiles:
        raise FormatVersionError(
            f"profiles.default in {path} is not read: the base is config.yml's top level",
            hint="move its sections to the top of the file",
        )


def _check_stamp(raw: dict[str, Any], path: Path, *, newest: int) -> None:
    if "format_version" not in raw:
        return
    value = raw["format_version"]
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise FormatVersionError(  # invalid stamps get the same write-path protection
            f"invalid format_version in {path}: expected a positive integer, got {value!r}"
        )
    if value > newest:
        raise NewerFormatError(
            f"{path} was written by a newer untaped (format {value}; "
            f"this release reads format {newest}); upgrade untaped",
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
    state_raw = load_state_yaml(state_path)
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


#: Top-level keys of ``state.yml`` that core owns (11.x's profile layout and
#: the on-disk format stamp), never usable as plugin state names.
RESERVED_STATE_SECTIONS = frozenset({"active", *FILE_KEYS})

#: ``state.yml`` sections core keeps its own rows in (:func:`untaped.sdk.storage.state.core_rows`).
CORE_STATE_SECTIONS = frozenset({"credentials"})


#: Config sections core owns: the top-level layout keys and the keys
#: injected into plugin sections (:data:`RESERVED_SECTION_KEYS`).
#: No plugin section or state section may take one, so a plugin named after
#: one is quarantined (``duplicate-name``).
RESERVED_SECTIONS = frozenset(
    {
        *RESERVED_SECTION_KEYS,
        *RESERVED_STATE_SECTIONS,
        *CORE_STATE_SECTIONS,
    }
)


def check_state_section_name(section: str, *, core: bool = False) -> None:
    """Reject a state section name that collides with ``config.yml``'s own keys.

    ``core=True`` takes only core's own sections (:data:`CORE_STATE_SECTIONS`).
    """
    if core:
        if section not in CORE_STATE_SECTIONS:
            raise ConfigError(f"{section!r} is not a state section core keeps")
        return
    if not section or section in RESERVED_SECTIONS:
        raise ConfigError(f"reserved or invalid state section name: {section!r}")


def resolve_state_path() -> Path:
    """``state.yml`` at the home's root (:func:`untaped.sdk.home`)."""
    return home().expanduser() / STATE_FILE_NAME


def resolve_config_path() -> Path:
    """``config.yml`` at the home's root (:func:`untaped.sdk.home`)."""
    return config_path()


@lru_cache(maxsize=1)
def get_settings_model() -> type[Settings]:
    """Build the current aggregate settings model from registered sections."""
    return _build_settings_model(_CONFIG_REGISTRY.profile_sections, _CONFIG_REGISTRY.state_sections)


@lru_cache(maxsize=1)
def get_profile_settings_model() -> type[Settings]:
    """Build the user-tunable profile settings model without top-level state."""
    return _build_settings_model(_CONFIG_REGISTRY.profile_sections, {})


def _build_settings() -> Settings:
    settings_cls = get_settings_model()
    try:
        return settings_cls()
    except ValidationError as exc:
        raise ConfigError(settings_error_message(exc, settings_cls)) from exc


class _SettingsGetter:
    """``get_settings``: the cached aggregate settings, uncached while an overlay is set.

    The cache is process-global, so candidate values must never reach it: with
    an overlay set the settings are built fresh and returned without being
    stored. Keeps ``cache_clear`` and ``cache_info`` of the ``lru_cache`` it
    wraps (many call sites drop the cache).
    """

    def __init__(self) -> None:
        self._cached = lru_cache(maxsize=1)(_build_settings)

    def __call__(self) -> Settings:
        """Return the cached aggregate settings instance."""
        if _overlay.get() is not None:
            return _build_settings()
        return self._cached()

    def cache_clear(self) -> None:
        """Drop the cached settings."""
        self._cached.cache_clear()

    def cache_info(self) -> Any:
        """The cache's hit and size counters (``functools.lru_cache``'s)."""
        return self._cached.cache_info()


get_settings = _SettingsGetter()


def validate_config_file(candidate: Path) -> None:
    """Validate ``candidate`` as if it were the config file, without loading it.

    Checks exactly what :func:`get_settings` would (environment overrides and
    plugin state included) against the candidate's content instead of
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
        candidate = env_var_name(loc[:depth])
        if candidate in os.environ:
            return candidate
        path = ".".join(loc[1:depth])
        for old in sorted(old for old, new in readable.items() if new == path):
            name = _env_name(loc[0], old)
            if _env_is_set(name):
                return name
    return None


def _readable_old_keys(settings_cls: type[BaseModel], section: str) -> dict[str, str]:
    model = model_sections(settings_cls).get(section)
    return {} if model is None else dict(key_mappings(model).readable)


def get_config_section[T: BaseModel](section: str, model_cls: type[T]) -> T:
    """Return one typed settings section, building a one-off model if needed.

    Only ``section`` (plus its own state section) is validated, so an invalid
    sibling section never breaks an unrelated plugin. A token-bearing
    section gets its token fallbacks applied (:func:`untaped.sdk.network.auth.resolve_token`).
    """
    settings_cls: type[Settings] | None = None
    if section not in _CONFIG_REGISTRY.profile_sections:
        settings_cls = _build_settings_model({section: model_cls}, _CONFIG_REGISTRY.state_sections)
    from untaped.sdk.network.auth import resolve_token  # noqa: PLC0415

    value = load_settings_section(section, settings_cls)
    if isinstance(value, BaseModel):
        # Resolve on the composed class, which holds the injected command settings.
        value = resolve_token(value, section=section)
    if isinstance(value, model_cls):
        return value
    if isinstance(value, BaseModel):
        return model_cls.model_validate(value.model_dump())
    return resolve_token(model_cls.model_validate(value), section=section)


def _build_settings_model(
    profile_sections: Mapping[str, type[BaseModel]],
    state_sections: Mapping[str, type[BaseModel]],
) -> type[Settings]:
    from untaped.core.settings import NAME, CoreSettings  # noqa: PLC0415 - core imports this module

    # Core's section is there before composition registers it too (``SHELL_SPEC``),
    # so a model built before composition (or for a test's own sections) has it.
    profile_sections = {NAME: CoreSettings, **profile_sections}
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


reset_config_registry()
