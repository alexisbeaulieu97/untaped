"""Internal plugin composition kernel.

Implements the plugin pipeline: discovery and metadata pre-checks, entry-point
resolution, declaration validation, quarantine of every claimant of a contested
name, then app-factory staging and commit. Every plugin,
first-party ones included, arrives as an entry-point candidate; every violation
yields a :class:`QuarantineRecord` while composition continues. Doctor-check
bodies never run here.

This module is intentionally NOT re-exported: plugin authors import the
stable surface from :mod:`untaped.sdk` instead.
"""

from __future__ import annotations

import functools
import hashlib
import logging
import re
import sys
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cached_property
from importlib import import_module
from importlib import metadata as importlib_metadata
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, TypeGuard, overload

from cyclopts import App
from packaging.markers import UndefinedComparison, UndefinedEnvironmentName
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion
from pydantic import BaseModel

from untaped.runtime.declaration import (
    Declaration,
    KeptDeclaration,
    PluginDecl,
    Problem,
    Quarantined,
)
from untaped.runtime.settings import (
    PLUGIN_NAME_PATTERN,
    RESERVED_SECTIONS,
    plugins_root,
    reserved_section_keys,
    section_model,
    timed_caches,
)
from untaped.sdk._home import HOME_ENV
from untaped.sdk.errors import ConfigError
from untaped.sdk.health.deprecated_keys import mapping_errors
from untaped.sdk.health.doctor import DoctorCheck
from untaped.sdk.health.marks import setup_mark_errors
from untaped.sdk.marks import (
    DeprecatedBehaviour,
    Stability,
    check_stability,
)
from untaped.sdk.network.credentials import (
    Credential,
    CredentialKey,
    credential_list,
    key_errors,
    key_fields,
)
from untaped.sdk.records import KindError
from untaped.sdk.storage.cache import Cache, Filled
from untaped.sdk.storage.store import Store

if TYPE_CHECKING:
    from untaped.runtime.composition import PluginEntry
    from untaped.runtime.mount import Mount
    from untaped.sdk.contracts import Contract
    from untaped.sdk.plugin import Plugin
    from untaped.sdk.progress import ProgressHandle
    from untaped.sdk.storage.holds import ItemStore

_LOG = logging.getLogger("untaped.registry")

#: Distribution whose version ``Requires-Dist: untaped`` is checked against.
_CORE_DISTRIBUTION = "untaped"

#: Entry-point group every plugin is discovered from.
PLUGINS_ENTRY_POINT_GROUP = "untaped.plugins"


@dataclass(frozen=True)
class SkillAsset:
    """The stub skill a plugin ships: core writes it from its own template and
    ``description``, and the stub sends the agent to ``untaped docs``. A
    plugin ships at most one.
    """

    name: str
    description: str = ""

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ConfigError("skill asset name must not be empty")
        if not self.description.strip():
            raise ConfigError(f"skill asset {self.name!r} must have a description")


def _check_model(model: object, field: str, label: str) -> None:
    if model is not None and not (isinstance(model, type) and issubclass(model, BaseModel)):
        raise ConfigError(f"{label} {field} must be a pydantic BaseModel subclass or None")


def check_plugin_name(name: str) -> None:
    """Raise :class:`ConfigError` unless ``name`` is a valid plugin name.

    ``@`` is kept for a later ``<plugin>@<instance>`` form, so it gets its
    own message.
    """
    if "@" in name:
        raise ConfigError(f"plugin name {name!r} must not contain '@' (reserved)")
    if not PLUGIN_NAME_PATTERN.fullmatch(name):
        raise ConfigError(
            f"plugin name {name!r} must be lowercase words joined by single hyphens "
            f"(pattern {PLUGIN_NAME_PATTERN.pattern})"
        )


def _no_deprecations() -> Sequence[DeprecatedBehaviour]:
    return ()


def _no_contracts() -> Sequence[type[Contract]]:
    return ()


@dataclass(frozen=True)
class PluginSpec:
    """One composable plugin unit.

    ``name`` is also the plugin's config section and CLI group. Every part
    is optional: ``app_factory`` builds the commands mounted under the name
    (none when it is ``None``) and ``settings`` is the settings model of the
    plugin's config section.

    ``help`` is the one-line summary shown in the Plugins panel of root help;
    a plugin with commands has one, and its ``app_factory`` (so its CLI
    import tree) runs only when one of its commands is dispatched.

    ``stability`` marks the whole plugin ``experimental`` or
    ``deprecated(...)``. It sits here, never on the app the factory returns:
    a lazy mount reads only the spec.

    ``contracts`` returns the :class:`~untaped.sdk.contracts.Contract` classes the
    plugin owns, and ``provides`` maps an owner plugin's name to a function
    returning this plugin's providers of that owner's contracts. Both are
    functions with a local import, so nothing under them is imported until a
    contract is first asked for (``untaped.sdk.contracts``).

    ``deprecations`` returns the plugin's deprecated behaviours
    (:func:`~untaped.sdk.marks.deprecated_behaviour`), a function with a local
    import for the same reason: each gets a ``behaviour`` line in the public
    surface, and its warnings name this plugin's distribution.

    ``caches`` are the :class:`~untaped.sdk.storage.cache.Cache` objects the plugin
    keeps; each gets the setting ``<name>.caches.<cache>.max_age_seconds``,
    except a version-only one (``max_age=None``), which has none.
    ``stores`` is what the plugin keeps on disk outside its settings: the
    :class:`~untaped.sdk.storage.store.Store` objects, each file in a layout of its own
    (``doctor`` lists them), and the :class:`~untaped.sdk.storage.holds.ItemStore`
    objects whose items projects may hold (core asks the plugin how to
    release them), which share one namespace.

    ``credentials`` are the :class:`~untaped.sdk.network.credentials.Credential` objects
    whose secrets the plugin needs: core keeps them outside configuration,
    owns ``untaped auth login`` for them.
    """

    name: str
    app_factory: Callable[[], App] | None = None
    settings: type[BaseModel] | None = None
    skills: tuple[SkillAsset, ...] = ()
    doctor_checks: tuple[DoctorCheck, ...] = ()
    help: str | None = None
    stability: Stability | None = None
    contracts: Callable[[], Sequence[type[Contract]]] = _no_contracts
    provides: Mapping[str, Callable[[], Sequence[Contract]]] = field(
        default_factory=lambda: MappingProxyType({}), hash=False
    )
    caches: tuple[Cache[Any], ...] = ()
    stores: tuple[Store | ItemStore[Any], ...] = ()
    credentials: tuple[Credential[Any], ...] = ()
    deprecations: Callable[[], Sequence[DeprecatedBehaviour]] = _no_deprecations

    def __post_init__(self) -> None:
        check_plugin_name(self.name)
        _check_model(self.settings, "settings", f"plugin {self.name!r}")
        if self.app_factory is not None and not callable(self.app_factory):
            raise ConfigError(f"plugin {self.name!r} app_factory must be callable or None")
        try:
            check_stability(self.stability, where=f"plugin {self.name!r}")
        except TypeError as exc:
            raise ConfigError(str(exc)) from exc
        if self.help is not None and (
            not isinstance(self.help, str) or not self.help.strip() or "\n" in self.help
        ):
            raise ConfigError(f"plugin {self.name!r} help must be a non-empty single line or None")
        if self.help is not None and self.app_factory is None:
            raise ConfigError(
                f"plugin {self.name!r} help describes commands it has no app_factory for"
            )
        _check_listings(self)
        object.__setattr__(self, "skills", tuple(self.skills))
        object.__setattr__(self, "doctor_checks", tuple(self.doctor_checks))
        object.__setattr__(self, "caches", tuple(self.caches))
        for cache in self.caches:
            if not isinstance(cache, Cache):
                raise ConfigError(f"plugin {self.name!r} caches holds {cache!r}, not a Cache")
        try:
            object.__setattr__(self, "credentials", credential_list(self.credentials))
        except TypeError as exc:
            raise ConfigError(f"plugin {self.name!r} {exc}") from None
        object.__setattr__(self, "stores", tuple(self.stores))
        for store in self.stores:
            if not isinstance(store, Store) and not _is_item_store(store):
                raise ConfigError(
                    f"plugin {self.name!r} stores holds {store!r}, not a Store or an ItemStore"
                )
        object.__setattr__(self, "provides", MappingProxyType(dict(self.provides)))


class DeclaredBlocks[B](Sequence[B]):
    """A 12.0 plugin's blocks of one kind, for its spec: counted from its declaration, loaded late.

    A start reads the declaration, not the plugin class, which imports every
    command; the class is imported the first time a block is read (``doctor``,
    ``setup``). Its caches' settings come from the declaration too
    (``timed``), so composing the settings section never imports it. This
    goes with the 11.x seam (:func:`untaped.runtime.mount.plugin_spec`).
    """

    def __init__(
        self,
        ids: tuple[str, ...],
        load: Callable[[], tuple[B, ...]],
        *,
        timed: tuple[Cache[Any], ...] = (),
    ) -> None:
        #: Each block's id (``<plugin>.<name>``), in the class's order.
        self.ids = ids
        self._load = load
        #: The caches with a max age, as their settings read them (caches only).
        self.timed = timed

    def __len__(self) -> int:
        return len(self.ids)

    def __iter__(self) -> Iterator[B]:
        # none declared: nothing to import the class for
        return iter(self._load() if self.ids else ())

    @overload
    def __getitem__(self, index: int) -> B: ...
    @overload
    def __getitem__(self, index: slice) -> tuple[B, ...]: ...
    def __getitem__(self, index: int | slice) -> B | tuple[B, ...]:
        return self._load()[index]

    def __repr__(self) -> str:
        return f"DeclaredBlocks({', '.join(self.ids)})"


def _check_listings(spec: PluginSpec) -> None:
    """``contracts``, ``deprecations`` and each ``provides`` entry are functions."""
    for listing in ("contracts", "deprecations"):
        if not callable(getattr(spec, listing)):
            raise ConfigError(f"plugin {spec.name!r} {listing} must be a function")
    for owner, providers in spec.provides.items():
        check_plugin_name(owner)
        if not callable(providers):
            raise ConfigError(f"plugin {spec.name!r} provides[{owner!r}] must be a function")


class _NoSettings(BaseModel):
    """The settings of a plugin that declares none: no fields."""


def owns_contracts(spec: PluginSpec) -> bool:
    """Whether ``spec`` declares contracts (read from the spec; nothing is imported)."""
    return spec.contracts is not _no_contracts


def contract_owners(result: CompositionResult) -> tuple[str, ...]:
    """The composed plugins that own contracts, which a ranking may name."""
    return tuple(each.spec.name for each in result.plugins if owns_contracts(each.spec))


def section_settings(spec: PluginSpec, *, extensions: bool = True) -> type[BaseModel] | None:
    """The model registered for ``spec``'s config section; ``None`` when it has none.

    The section of a contract owner, or of a plugin that declares caches,
    always exists: it carries the keys the SDK injects, ``extensions`` and
    ``caches`` (:func:`untaped.runtime.settings.section_model`). So does the
    section of a plugin declaring credentials, whose ``UNTAPED_<SECTION>__<NAME>``
    overrides one. ``extensions=False`` leaves ``extensions`` out (the config
    reference documents it elsewhere).
    """
    owner = extensions and owns_contracts(spec)
    caches = _timed_caches(spec)
    if owner or caches or spec.credentials:
        return section_model(
            spec.settings or _NoSettings,
            extensions=owner,
            caches=caches,
            section=spec.name,
            credentials=_credential_names(spec),
        )
    return spec.settings


def _credential_names(spec: PluginSpec) -> frozenset[str]:
    """The plugin's credential names; a 12.0 plugin's from its declaration, unimported."""
    if isinstance(spec.credentials, DeclaredBlocks):
        return frozenset(each.partition(".")[2] for each in spec.credentials.ids)
    return frozenset(each.name for each in spec.credentials)


def _timed_caches(spec: PluginSpec) -> tuple[Cache[Any], ...]:
    """The plugin's caches with a setting; a 12.0 plugin's from its declaration, unimported."""
    if isinstance(spec.caches, DeclaredBlocks):
        return spec.caches.timed
    return timed_caches(spec.caches)


def settings_model(spec: PluginSpec) -> type[BaseModel]:
    """The plugin's section model, or a model with no fields when it has none."""
    return section_settings(spec) or _NoSettings


def plugin_dir(spec: PluginSpec) -> Path:
    """The directory a plugin keeps its own data in: ``~/.untaped/plugins/<name>/``.

    It takes the spec rather than reading any ambient state, so it works
    from any thread. The directory is not created.
    """
    return named_plugin_dir(spec.name)


def named_plugin_dir(name: str) -> Path:
    """:func:`plugin_dir` by the plugin's name, for core code that holds a name, not a spec."""
    return plugins_root() / name


@dataclass(frozen=True)
class PluginRef:
    """How a composed plugin arrived.

    ``distribution_version`` and ``requires_dist`` are the distribution's
    metadata (contract providers check their owner's range against it);
    they don't take part in equality.
    """

    distribution: str
    entry_point: str
    distribution_version: str = field(default="", compare=False)
    requires_dist: tuple[str, ...] = field(default=(), compare=False)


@dataclass(frozen=True)
class RegisteredPlugin:
    """A fully validated, committed plugin."""

    spec: PluginSpec
    plugin_ref: PluginRef
    skills: tuple[SkillAsset, ...]
    #: Its commands mount at the root, read from its declaration
    #: (:attr:`~untaped.runtime.declaration.PluginDecl.root`): core's.
    root: bool = False


#: Every valid quarantine/diagnostic reason code lives here.
VALID_REASONS = frozenset(
    {
        "duplicate-name",
        "duplicate-doctor-check",
        "doctor-check-failed",
        "malformed-entry-point",
        "not-a-plugin-class",
        "bad-app-factory",
        "bad-metadata",
        "bad-settings-keys",
        "duplicate-kind",
        "foreign-kind",
        "bad-credential",
        "bad-plugin-class",
    }
)


@dataclass(frozen=True)
class QuarantineRecord:
    """Why a plugin was excluded.

    ``name`` is the candidate's entry-point (plugin) name.
    """

    name: str
    distribution: str
    entry_point: str
    reason: str
    detail: str

    def __post_init__(self) -> None:
        if self.reason not in VALID_REASONS:
            raise ConfigError(f"unknown quarantine reason: {self.reason!r}")
        if not self.detail.strip():
            raise ConfigError("quarantine detail must not be empty")


@dataclass(frozen=True)
class PluginCandidate:
    """One discovered candidate awaiting composition.

    ``target`` is the entry point's ``module:attr`` string, or the
    :class:`PluginSpec` itself for a plugin composed without installing it.
    ``distribution_version``, ``entry_point_group``, and ``requires_dist``
    are captured at discovery via :mod:`importlib.metadata` without importing
    plugin code; ``untaped plugin list`` reports ``distribution_version``
    for candidates.
    """

    distribution: str
    name: str
    target: object
    distribution_version: str = ""
    entry_point_group: str = PLUGINS_ENTRY_POINT_GROUP
    requires_dist: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "requires_dist", tuple(self.requires_dist))


@dataclass(frozen=True)
class CompositionResult:
    """Committed plugins plus quarantine records for one composition.

    ``plugins`` holds every plugin composed, core first: core is the one
    marked :attr:`RegisteredPlugin.root`, read from its declaration, and a
    reader that treats it apart reads that mark. ``candidates`` are the ones
    it was composed from, so a reader of the composition (``untaped plugin
    list``'s records) reads the same ones.
    """

    plugins: tuple[RegisteredPlugin, ...] = ()
    quarantine: tuple[QuarantineRecord, ...] = ()
    candidates: tuple[PluginCandidate, ...] = field(default=(), compare=False)
    #: The names no plugin may have, each with what holds it (``a config key``...).
    taken: Mapping[str, str] = field(default_factory=dict, compare=False)
    #: The plugin classes composed, core's included, mounted.
    mount: Mount = field(kw_only=True, compare=False)


def candidate_distribution(candidate: PluginCandidate) -> str:
    """The distribution a candidate is reported under; a blank one is ``unknown``."""
    return candidate.distribution.strip() or "unknown"


class _Quarantine(Exception):
    """Internal control flow: one plugin failed validation.

    :func:`compose` converts it to a :class:`QuarantineRecord`.
    """

    def __init__(self, reason: str, detail: str, entry_point: str | None = None) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail
        self.entry_point = entry_point
        #: Whether the candidate is a plugin class, which the declaration remembers refused.
        self.plugin_class = False

    def to_record(self, candidate: PluginCandidate) -> QuarantineRecord:
        distribution = candidate_distribution(candidate)
        entry_point = (
            self.entry_point if self.entry_point is not None else candidate_entry_point(candidate)
        )
        return QuarantineRecord(
            name=candidate.name,
            distribution=distribution,
            entry_point=entry_point,
            reason=self.reason,
            detail=self.detail,
        )


def parse_requirement(requirement: object) -> Requirement | None:
    """Parse one PEP 508 Requires-Dist string; ``None`` when malformed."""
    if not isinstance(requirement, str):
        return None
    try:
        return Requirement(requirement)
    except InvalidRequirement:
        return None


#: What ``requirement_admits`` raises on an entry whose marker or version it cannot evaluate.
UNEVALUABLE = (InvalidVersion, UndefinedComparison, UndefinedEnvironmentName, KeyError)


def requirement_admits(requirement: Requirement, sdk_version: str) -> bool:
    """Whether a parsed Requires-Dist entry admits ``sdk_version``.

    An entry whose environment marker does not apply (e.g. an ``extra``
    nobody requested) constrains nothing. A direct reference (PEP 508 URL)
    is a pinned source, not a version range, so admission cannot be
    disproved. Pre-releases of the running SDK are compared per PEP 440
    (``6.1.0.dev3`` does not satisfy ``>=6.1.0``).
    """
    if requirement.marker is not None and not requirement.marker.evaluate({"extra": ""}):
        return True
    if requirement.url:
        return True
    return requirement.specifier.contains(sdk_version, prereleases=True)


def plain_requirements(requires_dist: Sequence[str], distribution: str) -> list[Requirement]:
    """The requirements on ``distribution`` a default install pulls in: no ``extra`` marker.

    Another marker (``python_version``, ``sys_platform``) keeps a requirement plain.
    """
    wanted = canonicalize_name(distribution)
    return [
        parsed
        for parsed in (parse_requirement(line) for line in requires_dist)
        if parsed is not None
        and canonicalize_name(parsed.name) == wanted
        and (parsed.marker is None or not mentions_extra(str(parsed.marker)))
    ]


def owner_requirement(
    requires_dist: Sequence[str], owner: str, distribution: str | None = None
) -> Requirement | None:
    """The requirement a provider declares on ``owner``'s distribution.

    A 12.0 provider requires the plugin it fills contracts for in its plain
    dependencies, since a fill subclasses the owner's contract; an 11.x one
    declares it under an extra named after the owner (``untaped-acme[workspace]``).
    ``distribution`` is ``untaped-<owner>`` by default. A plain requirement
    (:func:`plain_requirements`) wins. The ``provides-requirement`` convention
    and the ``owner-out-of-range`` quarantine read this one requirement.
    """
    wanted = canonicalize_name(distribution or f"untaped-{owner}")
    plain = plain_requirements(requires_dist, wanted)
    if plain:
        return plain[0]
    found: Requirement | None = None
    for line in requires_dist:
        parsed = parse_requirement(line)
        if parsed is None or parsed.marker is None or canonicalize_name(parsed.name) != wanted:
            continue
        # The environment fills every other variable, so ``extra`` decides; a marker
        # naming one it can't (``extras``) adds no owner requirement.
        try:
            added = parsed.marker.evaluate({"extra": owner})
            added = added and not parsed.marker.evaluate({"extra": ""})
        except KeyError:
            continue
        if added and found is None:
            found = parsed
    return found


def declared_distributions(
    requirements: Sequence[str], extras: frozenset[str] = frozenset()
) -> set[str]:
    """Canonical names of the distributions ``requirements`` (its ``Requires-Dist``) declare.

    That is what a default install pulls in, plus what an extra in
    ``extras`` (the owner keys the plugin fills) adds: the owner a provider
    declares under ``untaped-acme[<owner>]``. Other extra-guarded
    requirements are left out; other markers (``python_version``,
    ``sys_platform``…) are ignored; invalid strings are skipped.
    """
    names: set[str] = set()
    for requirement in requirements:
        parsed = parse_requirement(requirement)
        if parsed is None:
            continue
        if (
            parsed.marker is None
            or not mentions_extra(str(parsed.marker))
            or _added_by(parsed, extras)
        ):
            names.add(canonicalize_name(parsed.name))
    return names


def _added_by(requirement: Requirement, extras: frozenset[str]) -> bool:
    """Whether one of ``extras`` turns ``requirement`` on."""
    marker = requirement.marker
    if marker is None:
        return False
    try:
        return any(marker.evaluate({"extra": extra}) for extra in extras)
    except KeyError:  # a variable the environment can't fill (``extras``)
        return False


def mentions_extra(marker: str) -> bool:
    """Whether ``marker`` tests the ``extra`` variable (quoted values aside)."""
    return re.search(r"\bextra\b", re.sub(r"\"[^\"]*\"|'[^']*'", "", marker)) is not None


def admits(requirement: Requirement, version: str) -> bool:
    """Whether ``requirement``'s range admits ``version`` (a direct reference always does)."""
    if requirement.url:
        return True
    try:
        return requirement.specifier.contains(version, prereleases=True)
    except InvalidVersion:
        return True


def range_text(requirement: Requirement) -> str:
    """``requirement``'s range as people write it, lower bound first: ``>=2,<3``."""
    return ",".join(
        sorted((str(each) for each in requirement.specifier), key=lambda each: each[0] != ">")
    )


def _running_sdk_version() -> str | None:
    """Running SDK version via importlib.metadata; None when unresolvable."""
    try:
        return importlib_metadata.version(_CORE_DISTRIBUTION)
    except importlib_metadata.PackageNotFoundError:
        return None


def _check_entry_point_group(candidate: PluginCandidate) -> None:
    if candidate.entry_point_group != PLUGINS_ENTRY_POINT_GROUP:
        raise _Quarantine(
            "bad-metadata",
            f"entry-point group {candidate.entry_point_group!r} of distribution "
            f"{candidate.distribution!r} is not the plugins group "
            f"{PLUGINS_ENTRY_POINT_GROUP!r}",
        )


def _check_requires_dist(candidate: PluginCandidate, state: _CompositionState) -> None:
    untaped_requirements: list[tuple[str, Requirement]] = []
    for requirement in candidate.requires_dist:
        parsed = parse_requirement(requirement)
        if parsed is None:
            raise _Quarantine(
                "bad-metadata",
                f"malformed Requires-Dist entry {requirement!r} of distribution "
                f"{candidate.distribution!r}",
            )
        if canonicalize_name(parsed.name) == _CORE_DISTRIBUTION:
            untaped_requirements.append((str(requirement), parsed))
    if not untaped_requirements:
        return
    sdk_version = state.sdk_version
    if sdk_version is None:
        raise _Quarantine(
            "bad-metadata",
            f"could not resolve running SDK version to check Requires-Dist "
            f"of distribution {candidate.distribution!r}",
        )
    for requirement, parsed in untaped_requirements:
        try:
            admits = requirement_admits(parsed, sdk_version)
        except UNEVALUABLE:
            raise _Quarantine(
                "bad-metadata",
                f"malformed Requires-Dist entry {requirement!r} of distribution "
                f"{candidate.distribution!r}",
            ) from None
        if not admits:
            raise _Quarantine(
                "bad-metadata",
                f"Requires-Dist {requirement!r} of distribution "
                f"{candidate.distribution!r} does not admit running SDK "
                f"version {sdk_version}",
            )


def discover_candidates(*, group: str = PLUGINS_ENTRY_POINT_GROUP) -> tuple[PluginCandidate, ...]:
    """Discover every plugin candidate from entry points.

    Reads distribution version, entry-point group, and Requires-Dist strings
    via :mod:`importlib.metadata` without importing any plugin code.
    """
    found: list[PluginCandidate] = []
    # Entry points of one distribution share its object; read its metadata once.
    read: dict[int, tuple[str, str, tuple[str, ...]]] = {}
    for entry_point in importlib_metadata.entry_points(group=group):
        dist = entry_point.dist
        if dist is None:
            found.append(
                PluginCandidate(
                    distribution="unknown",
                    name=entry_point.name,
                    target=entry_point.value,
                    entry_point_group=entry_point.group,
                )
            )
            continue
        if id(dist) not in read:
            name = dist.metadata.get("Name") or "unknown"
            read[id(dist)] = (str(name), dist.version, tuple(dist.requires or ()))
        dist_name, dist_version, requires = read[id(dist)]
        found.append(
            PluginCandidate(
                distribution=dist_name,
                name=entry_point.name,
                target=entry_point.value,
                distribution_version=dist_version,
                entry_point_group=entry_point.group,
                requires_dist=requires,
            )
        )
    return tuple(found)


class _CompositionState:
    """Mutable accumulation of one composition run (core + committed plugins)."""

    def __init__(self, core: PluginDecl) -> None:
        self.doctor_ids: set[str] = {
            f"{core.name}.{block.name}"
            for block in core.blocks
            if block.block == DoctorCheck.block_kind
        }
        #: The names no plugin may have, each with what holds it.
        self.taken: dict[str, str] = {
            **dict.fromkeys(RESERVED_SECTIONS, "a config key"),
            # ``UNTAPED_HOME`` would be read as a ``home`` section's whole value.
            "home": f"the variable {HOME_ENV}",
        }
        #: Core's name and its commands' words (:func:`_core`), held as ``taken`` is.
        self.core: dict[str, str] = {
            core.name: "core",
            **dict.fromkeys(core.command_words(), "a root command"),
        }

    @cached_property
    def sdk_version(self) -> str | None:
        """The running SDK version, resolved once per composition when first needed."""
        return _running_sdk_version()


def _check_free_name(name: str, state: _CompositionState) -> None:
    taken = state.taken.get(name) or state.core.get(name)
    if taken is not None:
        raise _Quarantine("duplicate-name", f"duplicate plugin name: {name!r} is taken by {taken}")


def _check_section_keys(spec: PluginSpec) -> None:
    # Only an owner's settings section gets ``extensions``, and only one with caches ``caches``.
    injected = frozenset(
        ({"extensions"} if owns_contracts(spec) else set())
        | ({"caches"} if _timed_caches(spec) else set())
    )
    taken = [] if spec.settings is None else reserved_section_keys(spec.settings, injected=injected)
    if taken:
        raise _Quarantine(
            "bad-settings-keys",
            f"plugin {spec.name!r} settings declares {', '.join(taken)}, "
            "which untaped reserves for its own section keys",
        )


def _check_doctor_checks(spec: PluginSpec, state: _CompositionState) -> None:
    seen_checks: set[str] = set()
    for check in spec.doctor_checks:
        if (
            not isinstance(check, DoctorCheck)
            or not check.id.strip()
            or not check.title.strip()
            or not callable(check.run)
        ):
            raise _Quarantine(
                "doctor-check-failed",
                f"malformed doctor check of plugin {spec.name!r}: {check!r}",
            )
        if check.id in state.doctor_ids or check.id in seen_checks:
            raise _Quarantine("duplicate-doctor-check", f"duplicate doctor id: {check.id!r}")
        seen_checks.add(check.id)


def _check_setup_marks(spec: PluginSpec) -> None:
    # ``NeedsSetup`` and ``Secret`` take effect only where core can act on them.
    if spec.settings is None:
        return
    errors = setup_mark_errors(spec.settings)
    if errors:
        raise _Quarantine("bad-settings-keys", f"plugin {spec.name!r}: {errors[0]}")


def _check_key_mappings(spec: PluginSpec) -> None:
    # The plugin's own model: an old key may never point at an injected key.
    if spec.settings is None:
        return
    errors = mapping_errors(spec.settings)
    if errors:
        raise _Quarantine("bad-settings-keys", f"plugin {spec.name!r}: {errors[0]}")


def _check_credentials(spec: PluginSpec) -> None:
    fields = {} if spec.settings is None else spec.settings.model_fields
    holders: dict[type[CredentialKey], list[str]] = {}
    if spec.settings is not None:
        for path, model in key_fields(spec.settings):
            holders.setdefault(model, []).append(".".join(path))
    for credential in spec.credentials:
        errors = key_errors(credential.key)
        if errors:
            raise _Quarantine(
                "bad-credential",
                f"plugin {spec.name!r} credential {credential.name!r}: {errors[0]}",
            )
        if credential.name in fields:
            raise _Quarantine(
                "bad-credential",
                f"plugin {spec.name!r} credential {credential.name!r} has the name of a "
                "setting of its section; a credential is not a setting",
            )
        held = holders.get(credential.key, [])
        if len(held) > 1:
            raise _Quarantine(
                "bad-credential",
                f"plugin {spec.name!r} credential {credential.name!r}: several settings hold a "
                f"{credential.key.__name__} ({', '.join(held)}); keep one",
            )


def _check_composed_mappings(spec: PluginSpec) -> None:
    # A cache's renamed_from exists on the composed model only: check it there.
    if not any(cache.renamed_from for cache in spec.caches):
        return
    try:
        errors = mapping_errors(settings_model(spec))
    except ConfigError as exc:
        errors = [str(exc)]
    if errors:
        raise _Quarantine(
            "bad-settings-keys", f"plugin {spec.name!r} caches renamed_from: {errors[0]}"
        )


def _check_declaration(spec: PluginSpec, state: _CompositionState) -> None:
    _check_free_name(spec.name, state)
    _check_own_declaration(spec)
    _check_doctor_checks(spec, state)


def _check_own_declaration(spec: PluginSpec) -> None:
    """The checks of ``spec``'s own declaration, no other plugin's needed: core's too."""
    _check_credentials(spec)
    _check_setup_marks(spec)
    _check_section_keys(spec)
    _check_key_mappings(spec)
    _check_composed_mappings(spec)


def _check_factory(factory: Callable[[], App]) -> App:
    try:
        staged = factory()
    except KindError as exc:
        raise _Quarantine(exc.reason, str(exc)) from None
    except Exception as exc:
        raise _Quarantine("bad-app-factory", f"{type(exc).__name__}: {exc}") from None
    return staged


def run_deferred_factory(plugin: RegisteredPlugin) -> App | QuarantineRecord:
    """The plugin's app, running its deferred factory; a failure as a quarantine record.

    The one place the factory runs, for first dispatch and ``untaped doctor``
    alike. A plugin without an ``app_factory`` has no app to build: asking
    for one is a caller bug and raises :class:`ConfigError`.
    """
    factory = plugin.spec.app_factory
    if factory is None:
        raise ConfigError(f"plugin {plugin.spec.name!r} has no commands to build")
    try:
        return _check_factory(factory)
    except _Quarantine as failed:
        ref = plugin.plugin_ref
        return QuarantineRecord(
            plugin.spec.name, ref.distribution, ref.entry_point, failed.reason, failed.detail
        )


def _commit(spec: PluginSpec, ref: PluginRef, state: _CompositionState) -> RegisteredPlugin:
    registered = RegisteredPlugin(spec=spec, plugin_ref=ref, skills=tuple(spec.skills))
    state.doctor_ids.update(_ids(spec.doctor_checks))
    return registered


def _ids(blocks: Sequence[DoctorCheck]) -> tuple[str, ...]:
    """Each block's id; a 12.0 plugin's from its declaration, so its class stays unimported."""
    if isinstance(blocks, DeclaredBlocks):
        return blocks.ids
    return tuple(block.id for block in blocks)


def resolve_target(target: object) -> object:
    """The object an entry point's ``module:attr`` target names (a non-string passes through)."""
    if not isinstance(target, str):
        return target
    module_name, sep, attr_path = target.partition(":")
    if not sep or not module_name.strip() or not attr_path.strip():
        raise ImportError(f"malformed entry-point target {target!r}: expected 'module:attr'")
    module = import_module(module_name)
    resolved: object = module
    for part in attr_path.split("."):
        resolved = getattr(resolved, part)
    return resolved


def compose(candidates: Sequence[PluginCandidate] = ()) -> CompositionResult:
    """Compose core, then every candidate in name order; quarantine failures.

    Core's own class (:class:`untaped.core.plugin.Core`) composes first, with
    no entry point: its name and its commands' words are taken, as are the
    config files' own keys, so a plugin named
    after one is quarantined (``duplicate-name``) and core always runs. It is
    the first of the result's plugins, marked ``root``.
    Each candidate is first provided and validated against core alone.
    A plugin name that two or more of the survivors
    claim is contested: every claimant is quarantined, so the result never
    depends on candidate order. The rest commit in ``(name, distribution,
    entry point)`` order, where a doctor-check id already taken quarantines
    the later one. A factory runs only at first dispatch and in
    ``untaped doctor`` (:func:`run_deferred_factory`), never in composition.

    The plugin classes' part, core's included, is kept in core's declaration cache
    (:data:`untaped.core.settings.DECLARATION`), keyed by the candidates:
    while this install's code is unchanged, a later composition of the same
    candidates reads it and imports no plugin class (each is imported the
    first time its code runs). A candidate given as an object, not an entry
    point's target, composes without it.
    """
    ordered = sorted(candidates, key=_candidate_order)
    if not all(isinstance(candidate.target, str) for candidate in ordered):
        return _compose(ordered)
    key, read = _declaration_key(ordered), _candidates_read(ordered)
    composed: list[CompositionResult] = []
    filling: list[bool] = []

    def fill(_key: str, _progress: ProgressHandle) -> Filled[KeptDeclaration]:
        filling.append(True)
        composed.append(_compose(ordered))
        return Filled(
            value=KeptDeclaration(candidates=read, declaration=composed[-1].mount.declaration)
        )

    from untaped.core.settings import DECLARATION  # noqa: PLC0415 - core imports the runtime

    try:
        served = DECLARATION.serve({key: "composed plugins"}, refresh=None, fill=fill)
        if not composed:
            try:
                kept = served.value[key]
                if kept.candidates != read:
                    raise _StaleDeclaration("the candidates changed")
                return _compose(ordered, kept.declaration)
            except Exception:
                # a candidate it doesn't account for, or a part of it that no longer loads
                # (a dependency gone): compose afresh and keep that instead
                _LOG.debug("the kept declaration does not compose", exc_info=True)
                DECLARATION.serve({key: "composed plugins"}, refresh=True, fill=fill)
    except Exception:
        if composed:
            return composed[-1]
        if filling:
            raise  # composing itself failed: no cache stands in for that
        # the cache failed (its version, its folder): compose without it, as before it existed
        _LOG.debug("composing without the declaration cache", exc_info=True)
        return _compose(ordered)
    return composed[-1]


def kept_declaration(candidates: Sequence[PluginCandidate]) -> Declaration | None:
    """The declaration core keeps for ``candidates``, read without composing, a lock or a fill.

    What the root's token pass reads command shapes from before anything is
    composed. ``None`` when none is kept for them: a candidate given as an
    object, a first start, candidates that changed, or a cache that can't be
    read (a home that doesn't resolve); composition finds and reports why.
    """
    ordered = sorted(candidates, key=_candidate_order)
    if not all(isinstance(candidate.target, str) for candidate in ordered):
        return None
    from untaped.core.settings import DECLARATION  # noqa: PLC0415 - core imports the runtime

    try:
        served = DECLARATION.cached(_declaration_key(ordered))
    except Exception:  # composition meets the same failure and reports it
        _LOG.debug("no kept declaration to read", exc_info=True)
        return None
    if served is None or served.value.candidates != _candidates_read(ordered):
        return None
    return served.value.declaration


def _declaration_key(ordered: Sequence[PluginCandidate]) -> str:
    """The cache key of ``ordered`` in this environment: each name, distribution and target.

    The environment (``sys.prefix``) is part of it, so two virtualenvs sharing
    one home keep a declaration each instead of replacing each other's.
    """
    listed = [sys.prefix] + [
        f"{candidate.name} {candidate.distribution} {candidate.target}" for candidate in ordered
    ]
    return _digest(listed)


def _candidates_read(ordered: Sequence[PluginCandidate]) -> str:
    """A digest of every candidate field composition reads, the key's and the metadata gates'."""
    return _digest(
        [
            repr(
                (
                    candidate.name,
                    candidate.distribution,
                    candidate.target,
                    candidate.distribution_version,
                    candidate.entry_point_group,
                    candidate.requires_dist,
                )
            )
            for candidate in ordered
        ]
    )


def _digest(lines: list[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()[:16]


class _StaleDeclaration(Exception):
    """The cached declaration does not account for a candidate: compose without it."""


def _compose(
    ordered: Sequence[PluginCandidate],
    declared: Declaration | None = None,
) -> CompositionResult:
    """:func:`compose` over ``ordered``; with ``declared``, its plugin classes come from it."""
    core = _core(declared)
    state = _CompositionState(core)
    quarantined: dict[int, QuarantineRecord] = {}
    provided: dict[int, type[Plugin]] = {}
    replayed: dict[int, PluginDecl] = {}
    classes: set[int] = set()  # the plugin classes, as :func:`_refused` reads them
    contested = _replay(ordered, declared, replayed, quarantined)
    for index, candidate in enumerate(ordered):
        if index in replayed or index in quarantined:
            continue
        try:
            provided[index] = _provide(candidate, state)
        except _Quarantine as failed:
            quarantined[index] = failed.to_record(candidate)
            if failed.plugin_class:
                classes.add(index)
        if declared is not None and (index in classes or index in provided):
            raise _StaleDeclaration(candidate.name)
    classes |= provided.keys()
    claimants = provided.keys() | replayed.keys() | contested
    for index, contest in _contested(ordered, claimants).items():
        quarantined.setdefault(index, contest.to_record(ordered[index]))
        provided.pop(index, None)
        replayed.pop(index, None)
    if declared is None:
        claims, mount = _mounted(ordered, provided, state, quarantined)
    else:
        claims, mount = _replayed(replayed, declared)
    plugins: list[RegisteredPlugin] = []
    for index, spec in claims.items():
        candidate = ordered[index]
        try:
            if index not in replayed:  # a replayed plugin passed these when it was composed
                _check_doctor_checks(spec, state)
        except _Quarantine as failed:
            quarantined[index] = failed.to_record(candidate)
            continue
        ref = PluginRef(
            distribution=candidate.distribution,
            entry_point=candidate_entry_point(candidate),
            distribution_version=candidate.distribution_version,
            requires_dist=candidate.requires_dist,
        )
        plugins.append(_commit(spec, ref, state))
    if declared is None:
        # Only a class the mount composed leaves it; one refused before it never got there.
        dropped = {ordered[index].name for index in classes & quarantined.keys() & provided.keys()}
        mount = mount.kept(_refused(ordered, classes, quarantined, mount), dropped=dropped)
    plugins.insert(0, _root_plugin(mount, core))
    return CompositionResult(
        plugins=tuple(plugins),
        quarantine=tuple(quarantined[index] for index in sorted(quarantined)),
        candidates=tuple(ordered),
        mount=mount,
        taken={**state.taken, **state.core},
    )


def _replay(
    ordered: Sequence[PluginCandidate],
    declared: Declaration | None,
    replayed: dict[int, PluginDecl],
    quarantined: dict[int, QuarantineRecord],
) -> set[int]:
    """Each candidate ``declared`` composed or refused, unimported; the ones it found contested.

    A contested one is still a claimant, so the plugin sharing its name is quarantined again.
    """
    if declared is None:
        return set()
    known: dict[tuple[str, str], PluginDecl | Quarantined] = {
        (each.name, each.target): each for each in declared.quarantined
    }
    known.update({(each.name, each.target): each for each in declared.plugins})
    contested: set[int] = set()
    for index, candidate in enumerate(ordered):
        found = known.get((candidate.name, candidate_entry_point(candidate)))
        if isinstance(found, PluginDecl):
            replayed[index] = found
        elif found is not None:
            quarantined[index] = _Quarantine(found.reason, found.detail).to_record(candidate)
            if found.reason == "duplicate-name":
                contested.add(index)
    return contested


def _refused(
    ordered: Sequence[PluginCandidate],
    classes: set[int],
    quarantined: Mapping[int, QuarantineRecord],
    mount: Mount,
) -> tuple[Quarantined, ...]:
    """Each plugin class quarantined, as the declaration remembers it."""
    refused: list[Quarantined] = []
    for index in sorted(classes & quarantined.keys()):
        record, candidate = quarantined[index], ordered[index]
        problems = mount.problems.get(candidate.name) or (
            Problem(rule=record.reason, where=candidate.name, detail=record.detail),
        )
        refused.append(
            Quarantined(
                name=candidate.name,
                target=candidate_entry_point(candidate),
                problems=problems,
                reason=record.reason,
            )
        )
    return tuple(refused)


def _root_plugin(mount: Mount, core: PluginDecl) -> RegisteredPlugin:
    """Core as one of the composed plugins: the plugin its declaration marks ``root``.

    No entry point names it, so its reference is its distribution and its
    class's target; its version is read only where it is shown, so composing
    reads no metadata for it.
    """
    from untaped.runtime.mount import plugin_spec  # noqa: PLC0415 - mount imports this module

    spec = plugin_spec(mount, core.name)
    ref = PluginRef(distribution=_CORE_DISTRIBUTION, entry_point=core.target)
    return RegisteredPlugin(spec=spec, plugin_ref=ref, skills=spec.skills, root=core.root)


def _core_entry() -> PluginEntry:
    """Core's own entry: no entry point names it, and its commands mount at the root."""
    from untaped.core.plugin import NAME, Core  # noqa: PLC0415 - imports every command of core's
    from untaped.runtime.composition import PluginEntry  # noqa: PLC0415
    from untaped.runtime.declaration import target_of  # noqa: PLC0415

    return PluginEntry(NAME, target_of(Core), Core, root=True)


def _core(declared: Declaration | None) -> PluginDecl:
    """Core's declaration: the kept one's, else composed now, so its words are taken first."""
    from untaped.core.settings import NAME  # noqa: PLC0415 - core imports the runtime

    if declared is not None:
        try:
            return declared.plugin(NAME)
        except LookupError:
            raise _StaleDeclaration(NAME) from None
    return _core_alone()


@functools.cache
def _core_alone() -> PluginDecl:
    """Core's class composed on its own, once a process: it is the same at every composition.

    Its own declaration gets the checks a plugin's gets (its settings' key
    declarations among them); a failure is a core bug no install can work
    around, so it raises instead of quarantining.
    """
    from untaped.core.settings import NAME  # noqa: PLC0415 - core imports the runtime
    from untaped.runtime.composition import compose as compose_classes  # noqa: PLC0415
    from untaped.runtime.mount import Mount, plugin_spec  # noqa: PLC0415

    composed = compose_classes([_core_entry()])
    for refused in composed.quarantined:
        raise RuntimeError(f"core's own class does not compose: {refused.detail}")
    try:
        _check_own_declaration(plugin_spec(Mount.of(composed), NAME))
    except _Quarantine as failed:
        raise RuntimeError(f"core's own class does not compose: {failed.detail}") from None
    return composed.plugin(NAME)


def _mounted(
    ordered: Sequence[PluginCandidate],
    provided: dict[int, type[Plugin]],
    state: _CompositionState,
    quarantined: dict[int, QuarantineRecord],
) -> tuple[dict[int, PluginSpec], Mount]:
    """Every claim as a spec, and the mount: core's class and the plugins' composed in one walk.

    A plugin class composition refuses is quarantined with every problem it
    names; one it composes reaches the rest of composition as the spec the
    mount builds for it (:func:`untaped.runtime.mount.plugin_spec`).
    """
    classes = dict(provided)
    from untaped.runtime.composition import PluginEntry  # noqa: PLC0415 - 12.0 plugins only
    from untaped.runtime.mount import compose_plugins, plugin_spec  # noqa: PLC0415

    problems: dict[str, tuple[Problem, ...]] = {}
    while True:
        mount = compose_plugins(
            [
                _core_entry(),
                *(
                    PluginEntry(ordered[index].name, _entry_target(ordered[index], cls), cls)
                    for index, cls in classes.items()
                ),
            ],
            taken=state.taken,
        )
        refused = {each.name: each for each in mount.declaration.quarantined}
        problems.update({name: each.problems for name, each in refused.items()})
        mounted: dict[int, PluginSpec] = {}
        for index in classes:
            candidate = ordered[index]
            try:
                if candidate.name in refused:
                    raise _Quarantine("bad-plugin-class", refused[candidate.name].detail)
                spec = plugin_spec(mount, candidate.name)
                _check_declaration(spec, state)
            except _Quarantine as failed:
                quarantined[index] = failed.to_record(candidate)
                continue
            mounted[index] = spec
        if len(mounted) == len(classes):
            mount.problems.update(problems)
            return dict(sorted(mounted.items())), mount
        # A class quarantined here must not shape the others' declaration
        # (a kind it owned, a command another one derives): compose without it.
        classes = {index: cls for index, cls in classes.items() if index in mounted}


def _replayed(
    replayed: Mapping[int, PluginDecl], declared: Declaration
) -> tuple[dict[int, PluginSpec], Mount]:
    """Every claim as a spec, read from ``declared``, no plugin class imported."""
    from untaped.runtime.mount import Mount, plugin_spec  # noqa: PLC0415 - 12.0 plugins only

    mount = Mount.of(declared)
    claims = {index: plugin_spec(mount, plugin.name) for index, plugin in replayed.items()}
    return dict(sorted(claims.items())), mount


def _is_plugin_class(obj: object) -> TypeGuard[type[Plugin]]:
    """Whether ``obj`` is a 12.0 plugin class, without importing the plugin interface.

    A plugin class subclasses :class:`untaped.sdk.plugin.Plugin`, so its import
    loaded that module; a 11.x composition never pays for it.
    """
    module = sys.modules.get("untaped.sdk.plugin")
    return module is not None and isinstance(obj, type) and issubclass(obj, module.Plugin)


def _entry_target(candidate: PluginCandidate, cls: type[Plugin]) -> str:
    """The ``module:Qualname`` an entry point names: its target, or the class given directly."""
    from untaped.runtime.declaration import target_of  # noqa: PLC0415 - 12.0 plugins only

    return candidate.target if isinstance(candidate.target, str) else target_of(cls)


def _contested(ordered: Sequence[PluginCandidate], claims: Iterable[int]) -> dict[int, _Quarantine]:
    """A quarantine per claimant of a plugin name two or more claims share.

    The name is also the config section and CLI group, so one contest
    covers all three. A claim carries its candidate's name (a spec's name
    was checked against it).
    """
    claimants: defaultdict[str, list[int]] = defaultdict(list)
    for index in claims:
        claimants[ordered[index].name].append(index)
    contested: dict[int, _Quarantine] = {}
    for name, indexes in claimants.items():
        if len(indexes) < 2:
            continue
        claimed_by = ", ".join(
            repr(dist) for dist in sorted(ordered[index].distribution for index in indexes)
        )
        for index in indexes:
            contested[index] = _Quarantine(
                "duplicate-name", f"duplicate plugin name: {name!r} (claimed by {claimed_by})"
            )
    return contested


def _candidate_order(candidate: PluginCandidate) -> tuple[str, str, str]:
    return (candidate.name, candidate.distribution, candidate_entry_point(candidate))


def candidate_entry_point(candidate: PluginCandidate) -> str:
    """The entry point a candidate's plugin records carry: its target, else its name."""
    return candidate.target if isinstance(candidate.target, str) else candidate.name


def _describe(value: object) -> str:
    """How a ``not-a-plugin-class`` detail names what an entry point resolved to."""
    if callable(value):
        return f"the callable {getattr(value, '__qualname__', repr(value))!r}"
    return f"a {type(value).__name__}"


def _package_of(candidate: PluginCandidate) -> str:
    """The import package a ``not-a-plugin-class`` hint names: the target's, else the name's."""
    target = candidate.target
    if isinstance(target, str) and ":" in target:
        return target.partition(":")[0].partition(".")[0]
    return "untaped_" + candidate.name.replace("-", "_")


def _class_name(candidate: PluginCandidate) -> str:
    """The class a ``not-a-plugin-class`` hint names: the plugin name in camel case."""
    return "".join(part.capitalize() for part in candidate.name.split("-"))


def _provide(candidate: PluginCandidate, state: _CompositionState) -> type[Plugin]:
    """Resolve one candidate's entry point to its plugin class.

    The entry point names the class (``untaped_acme.plugin:Acme``), which
    composition reads with the other plugin classes (:func:`_mounted`);
    anything else, an 11.x ``PluginSpec`` or a callable included, is
    ``not-a-plugin-class`` and is never called.

    Raises :class:`_Quarantine` on the first failed check.
    """
    # Metadata-only gates precede any import: group and Requires-Dist
    # admission are decided from distribution metadata without executing
    # plugin code.
    _check_entry_point_group(candidate)
    _check_requires_dist(candidate, state)
    try:
        spec = resolve_target(candidate.target)
    except KindError as exc:
        raise _Quarantine(exc.reason, str(exc), entry_point="") from None
    except Exception as exc:
        raise _Quarantine(
            "malformed-entry-point",
            f"could not resolve entry point {candidate.target!r} of "
            f"distribution {candidate.distribution!r}: {exc}",
            entry_point="",
        ) from None
    if _is_plugin_class(spec):
        try:
            _check_distribution(candidate)
            _check_free_name(candidate.name, state)
        except _Quarantine as failed:
            failed.plugin_class = True
            raise
        return spec
    what = "an 11.x PluginSpec" if isinstance(spec, PluginSpec) else _describe(spec)
    raise _Quarantine(
        "not-a-plugin-class",
        f"entry point {candidate.name!r} of distribution {candidate.distribution!r} "
        f"names {what}, not a plugin class; untaped 12.0 composes plugin classes: "
        f"name the class ('{_package_of(candidate)}.plugin:{_class_name(candidate)}')",
    )


def _check_distribution(candidate: PluginCandidate) -> None:
    if not candidate.distribution.strip():
        raise _Quarantine(
            "bad-metadata",
            f"plugin {candidate.name!r} declares an empty distribution name",
        )


def _is_item_store(value: object) -> bool:
    from untaped.sdk.storage.holds import ItemStore  # noqa: PLC0415 - kept off the startup path

    return isinstance(value, ItemStore)
