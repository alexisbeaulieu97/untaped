"""The models of a plugin's config section: its own, its contracts' and its caches'.

:func:`section_model` builds the model a plugin's section validates
against: the plugin's settings model with ``extensions`` (one
:class:`ExtensionSettings` per contract it owns) and ``caches`` (one
:class:`CacheSettings` subclass per cache it declares) injected.
"""

from __future__ import annotations

import re
import weakref
from collections.abc import Iterable, Mapping
from functools import cache
from types import MappingProxyType
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, cast

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    create_model,
)

from untaped.sdk._run import SECTION_OF
from untaped.sdk.errors import ConfigError
from untaped.sdk.health.deprecated_keys import Renamed

if TYPE_CHECKING:
    from untaped.sdk.storage.cache import Cache


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


#: Keys the SDK injects into a plugin's section, so its own settings model may
#: not declare them: ``extensions`` on every contract owner,
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
    ``untaped.unknown-keys`` row names it, and it never breaks the plugin's commands.
    """

    model_config = ConfigDict(extra="ignore")


@cache
def section_model(
    model: type[BaseModel],
    *,
    extensions: bool,
    caches: tuple[Cache[Any], ...],
    section: str = "",
    credentials: frozenset[str] = frozenset(),
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
    owner's own declarations never point at an injected key. ``credentials``
    names the section's credentials, which ``UNTAPED_<SECTION>__<NAME>`` overrides.
    """
    caches = timed_caches(caches)
    declared = model
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
        CREDENTIAL_NAMES[composed] = credentials
    setattr(composed, SECTION_OF, declared)  # its owner's model, which owner_of_settings reads
    return composed


#: Each composed section model's credential names: ``UNTAPED_<SECTION>__<NAME>`` overrides one.
CREDENTIAL_NAMES: weakref.WeakKeyDictionary[type[BaseModel], frozenset[str]] = (
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
