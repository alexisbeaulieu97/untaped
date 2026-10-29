"""Internal plugin composition kernel.

Implements the plugin pipeline: discovery and metadata pre-checks, entry-point
resolution, composition of the plugin classes and the checks of each one's
declaration, quarantine of every claimant of a contested name, then commit.
Every plugin, first-party ones included, arrives as an entry-point candidate;
every violation yields a :class:`QuarantineRecord` while composition
continues. A plugin's commands are built only when first dispatched, and
doctor-check bodies never run here.

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
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cached_property
from importlib import import_module
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import TYPE_CHECKING, TypeGuard

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
)
from untaped.sdk._home import HOME_ENV
from untaped.sdk.errors import ConfigError
from untaped.sdk.health.deprecated_keys import mapping_errors
from untaped.sdk.health.marks import setup_mark_errors
from untaped.sdk.network.credentials import (
    CredentialKey,
    key_errors,
    key_fields,
)
from untaped.sdk.storage.cache import Filled

if TYPE_CHECKING:
    from untaped.runtime.composed import ComposedPlugin
    from untaped.runtime.composition import PluginEntry
    from untaped.runtime.mount import Mount
    from untaped.sdk.plugin import Plugin
    from untaped.sdk.progress import ProgressHandle

_LOG = logging.getLogger("untaped.registry")

#: Distribution whose version ``Requires-Dist: untaped`` is checked against.
_CORE_DISTRIBUTION = "untaped"

#: Entry-point group every plugin is discovered from.
PLUGINS_ENTRY_POINT_GROUP = "untaped.plugins"


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


def contract_owners(result: CompositionResult) -> tuple[str, ...]:
    """The composed plugins that own contracts, which a ranking may name."""
    return tuple(each.name for each in result.plugins if each.owns_contracts)


def section_settings(plugin: ComposedPlugin, *, extensions: bool = True) -> type[BaseModel]:
    """The model registered for ``plugin``'s config section.

    The section of a contract owner, or of a plugin that declares caches,
    always exists: it carries the keys the SDK injects, ``extensions`` and
    ``caches`` (:func:`untaped.runtime.settings.section_model`). So does the
    section of a plugin declaring credentials, whose ``UNTAPED_<SECTION>__<NAME>``
    overrides one. ``extensions=False`` leaves ``extensions`` out (the config
    reference documents it elsewhere).
    """
    owner = extensions and plugin.owns_contracts
    if owner or plugin.timed_caches or plugin.credential_names:
        return section_model(
            plugin.settings,
            extensions=owner,
            caches=plugin.timed_caches,
            section=plugin.name,
            credentials=plugin.credential_names,
        )
    return plugin.settings


def settings_model(plugin: ComposedPlugin) -> type[BaseModel]:
    """The plugin's section model, the keys core injects included."""
    return section_settings(plugin)


def named_plugin_dir(name: str) -> Path:
    """The folder a plugin keeps its own data in, ``plugins/<name>/`` under the home, uncreated."""
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


#: Every valid quarantine/diagnostic reason code lives here.
VALID_REASONS = frozenset(
    {
        "duplicate-name",
        "malformed-entry-point",
        "not-a-plugin-class",
        "bad-app-factory",
        "bad-metadata",
        "bad-settings-keys",
        "duplicate-kind",
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

    ``target`` is the entry point's ``module:attr`` string, or the plugin
    class itself for a plugin composed without installing it.
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
    marked :attr:`~untaped.runtime.composed.ComposedPlugin.root`, read from its declaration, and a
    reader that treats it apart reads that mark. ``candidates`` are the ones
    it was composed from, so a reader of the composition (``untaped plugin
    list``'s records) reads the same ones.
    """

    plugins: tuple[ComposedPlugin, ...] = ()
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


def _check_entry_point_name(candidate: PluginCandidate) -> None:
    try:
        check_plugin_name(candidate.name)
    except ConfigError as exc:
        raise _Quarantine(
            "bad-metadata", f"entry point of distribution {candidate.distribution!r}: {exc}"
        ) from None


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


def _check_section_keys(plugin: ComposedPlugin) -> None:
    # Only an owner's settings section gets ``extensions``, and only one with caches ``caches``.
    injected = frozenset(
        ({"extensions"} if plugin.owns_contracts else set())
        | ({"caches"} if plugin.timed_caches else set())
    )
    taken = reserved_section_keys(plugin.settings, injected=injected)
    if taken:
        raise _Quarantine(
            "bad-settings-keys",
            f"plugin {plugin.name!r} settings declares {', '.join(taken)}, "
            "which untaped reserves for its own section keys",
        )


def _check_setup_marks(plugin: ComposedPlugin) -> None:
    # ``NeedsSetup`` and ``Secret`` take effect only where core can act on them.
    errors = setup_mark_errors(plugin.settings)
    if errors:
        raise _Quarantine("bad-settings-keys", f"plugin {plugin.name!r}: {errors[0]}")


def _check_key_mappings(plugin: ComposedPlugin) -> None:
    # The plugin's own model: an old key may never point at an injected key.
    errors = mapping_errors(plugin.settings)
    if errors:
        raise _Quarantine("bad-settings-keys", f"plugin {plugin.name!r}: {errors[0]}")


def _check_credentials(plugin: ComposedPlugin) -> None:
    fields = plugin.settings.model_fields
    holders: dict[type[CredentialKey], list[str]] = {}
    for path, model in key_fields(plugin.settings):
        holders.setdefault(model, []).append(".".join(path))
    for credential in plugin.credentials:
        errors = key_errors(credential.key)
        if errors:
            raise _Quarantine(
                "bad-credential",
                f"plugin {plugin.name!r} credential {credential.name!r}: {errors[0]}",
            )
        if credential.name in fields:
            raise _Quarantine(
                "bad-credential",
                f"plugin {plugin.name!r} credential {credential.name!r} has the name of a "
                "setting of its section; a credential is not a setting",
            )
        held = holders.get(credential.key, [])
        if len(held) > 1:
            raise _Quarantine(
                "bad-credential",
                f"plugin {plugin.name!r} credential {credential.name!r}: several settings hold a "
                f"{credential.key.__name__} ({', '.join(held)}); keep one",
            )


def _check_composed_mappings(plugin: ComposedPlugin) -> None:
    # A cache's renamed_from exists on the composed model only: check it there.
    if not any(cache.renamed_from for cache in plugin.timed_caches):
        return
    try:
        errors = mapping_errors(settings_model(plugin))
    except ConfigError as exc:
        errors = [str(exc)]
    if errors:
        raise _Quarantine(
            "bad-settings-keys", f"plugin {plugin.name!r} caches renamed_from: {errors[0]}"
        )


def _check_declaration(plugin: ComposedPlugin) -> None:
    """The checks of ``plugin``'s own declaration, no other plugin's needed: core's too."""
    _check_credentials(plugin)
    _check_setup_marks(plugin)
    _check_section_keys(plugin)
    _check_key_mappings(plugin)
    _check_composed_mappings(plugin)


def run_deferred_factory(plugin: ComposedPlugin) -> App | QuarantineRecord:
    """The plugin's app, built from its declaration; a failure as a quarantine record.

    The one place it is built, for first dispatch and ``untaped doctor``
    alike. A plugin without commands has no app to build: asking for one is a
    caller bug and raises :class:`ConfigError`.
    """
    if not plugin.has_commands:
        raise ConfigError(f"plugin {plugin.name!r} has no commands to build")
    try:
        return plugin.build_app()
    except Exception as exc:
        ref = plugin.plugin_ref
        return QuarantineRecord(
            plugin.name,
            ref.distribution,
            ref.entry_point,
            "bad-app-factory",
            f"{type(exc).__name__}: {exc}",
        )


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
    entry point)`` order. A plugin's commands are built only at first
    dispatch and in ``untaped doctor`` (:func:`run_deferred_factory`), never
    in composition.

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
        claims, mount = _replayed(ordered, replayed, declared)
    plugins = list(claims.values())
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


def _root_plugin(mount: Mount, core: PluginDecl) -> ComposedPlugin:
    """Core as one of the composed plugins: the plugin its declaration marks ``root``.

    No entry point names it, so its reference is its distribution and its
    class's target; its version is read only where it is shown, so composing
    reads no metadata for it.
    """
    return _composed(mount, core.name, _core_ref(core.target))


def _core_ref(target: str) -> PluginRef:
    """How core arrived: its distribution and its class's target, no entry point."""
    return PluginRef(distribution=_CORE_DISTRIBUTION, entry_point=target)


def _ref(candidate: PluginCandidate) -> PluginRef:
    """How ``candidate`` arrived: its distribution, entry point and metadata."""
    return PluginRef(
        distribution=candidate.distribution,
        entry_point=candidate_entry_point(candidate),
        distribution_version=candidate.distribution_version,
        requires_dist=candidate.requires_dist,
    )


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
    from untaped.runtime.mount import Mount  # noqa: PLC0415

    composed = compose_classes([_core_entry()])
    for refused in composed.quarantined:
        raise RuntimeError(f"core's own class does not compose: {refused.detail}")
    try:
        core = composed.plugin(NAME)
        _check_declaration(_composed(Mount.of(composed), NAME, _core_ref(core.target)))
    except _Quarantine as failed:
        raise RuntimeError(f"core's own class does not compose: {failed.detail}") from None
    return core


def _mounted(
    ordered: Sequence[PluginCandidate],
    provided: dict[int, type[Plugin]],
    state: _CompositionState,
    quarantined: dict[int, QuarantineRecord],
) -> tuple[dict[int, ComposedPlugin], Mount]:
    """Every claim composed, and the mount: core's class and the plugins' composed in one walk.

    A plugin class composition refuses is quarantined with every problem it
    names; one it composes is read through its :class:`ComposedPlugin`.
    """
    classes = dict(provided)
    from untaped.runtime.composition import PluginEntry  # noqa: PLC0415 - 12.0 plugins only
    from untaped.runtime.mount import compose_plugins  # noqa: PLC0415

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
        mounted: dict[int, ComposedPlugin] = {}
        for index in classes:
            candidate = ordered[index]
            try:
                if candidate.name in refused:
                    raise _Quarantine("bad-plugin-class", refused[candidate.name].detail)
                composed = _composed(mount, candidate.name, _ref(candidate))
                _check_declaration(composed)
            except _Quarantine as failed:
                quarantined[index] = failed.to_record(candidate)
                continue
            mounted[index] = composed
        if len(mounted) == len(classes):
            mount.problems.update(problems)
            return dict(sorted(mounted.items())), mount
        # A class quarantined here must not shape the others' declaration
        # (a kind it owned, a command another one derives): compose without it.
        classes = {index: cls for index, cls in classes.items() if index in mounted}


def _replayed(
    ordered: Sequence[PluginCandidate], replayed: Mapping[int, PluginDecl], declared: Declaration
) -> tuple[dict[int, ComposedPlugin], Mount]:
    """Every claim composed, read from ``declared``, no plugin class imported."""
    from untaped.runtime.mount import Mount  # noqa: PLC0415 - mount imports this module

    mount = Mount.of(declared)
    claims = {
        index: _composed(mount, plugin.name, _ref(ordered[index]))
        for index, plugin in replayed.items()
    }
    return dict(sorted(claims.items())), mount


def _is_plugin_class(obj: object) -> TypeGuard[type[Plugin]]:
    """Whether ``obj`` is a 12.0 plugin class, without importing the plugin interface.

    A plugin class subclasses :class:`untaped.sdk.plugin.Plugin`, so its import
    loaded that module; a 11.x composition never pays for it.
    """
    module = sys.modules.get("untaped.sdk.plugin")
    return module is not None and isinstance(obj, type) and issubclass(obj, module.Plugin)


def _composed(mount: Mount, name: str, plugin_ref: PluginRef) -> ComposedPlugin:
    """The composed plugin ``name`` of ``mount``, as core's readers read it."""
    from untaped.runtime.composed import ComposedPlugin  # noqa: PLC0415 - imports the SDK blocks

    return ComposedPlugin.of(mount, name, plugin_ref)


def _entry_target(candidate: PluginCandidate, cls: type[Plugin]) -> str:
    """The ``module:Qualname`` an entry point names: its target, or the class given directly."""
    from untaped.runtime.declaration import target_of  # noqa: PLC0415 - 12.0 plugins only

    return candidate.target if isinstance(candidate.target, str) else target_of(cls)


def _contested(ordered: Sequence[PluginCandidate], claims: Iterable[int]) -> dict[int, _Quarantine]:
    """A quarantine per claimant of a plugin name two or more claims share.

    The name is also the config section and CLI group, so one contest
    covers all three. A claim carries its candidate's name.
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
    anything else, an 11.x ``SPEC`` or a callable included, is
    ``not-a-plugin-class`` and is never called.

    Raises :class:`_Quarantine` on the first failed check.
    """
    # Metadata-only gates precede any import: group and Requires-Dist
    # admission are decided from distribution metadata without executing
    # plugin code.
    _check_entry_point_group(candidate)
    _check_entry_point_name(candidate)
    _check_requires_dist(candidate, state)
    try:
        found = resolve_target(candidate.target)
    except Exception as exc:
        raise _Quarantine(
            "malformed-entry-point",
            f"could not resolve entry point {candidate.target!r} of "
            f"distribution {candidate.distribution!r}: {exc}",
            entry_point="",
        ) from None
    if _is_plugin_class(found):
        try:
            _check_distribution(candidate)
            _check_free_name(candidate.name, state)
        except _Quarantine as failed:
            failed.plugin_class = True
            raise
        return found
    raise _Quarantine(
        "not-a-plugin-class",
        f"entry point {candidate.name!r} of distribution {candidate.distribution!r} "
        f"names {_describe(found)}, not a plugin class; untaped 12.0 composes plugin classes: "
        f"name the class ('{_package_of(candidate)}.plugin:{_class_name(candidate)}')",
    )


def _check_distribution(candidate: PluginCandidate) -> None:
    if not candidate.distribution.strip():
        raise _Quarantine(
            "bad-metadata",
            f"plugin {candidate.name!r} declares an empty distribution name",
        )
