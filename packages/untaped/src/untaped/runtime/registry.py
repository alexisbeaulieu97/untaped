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
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import cached_property
from importlib import import_module
from importlib import metadata as importlib_metadata
from pathlib import Path, PurePath
from typing import TYPE_CHECKING, TypeGuard

from cyclopts import App
from packaging.utils import canonicalize_name
from pydantic import BaseModel

from untaped.runtime.declaration import (
    Declaration,
    KeptDeclaration,
    PluginDecl,
    Problem,
    Quarantined,
    UseDecl,
)
from untaped.runtime.plugin_imports import ImportRecord, read_imports
from untaped.runtime.plugin_uses import (
    Owner,
    filled_uses,
    look_up,
    missing_words,
    owners_of,
)
from untaped.runtime.requirements import (
    UNEVALUABLE,
    Installed,
    Malformed,
    applies,
    distribution_present,
    is_exact,
    is_plain,
    parse_requirement,
    plain_requirements,
    unmet,
)
from untaped.runtime.section_models import PLUGIN_NAME_PATTERN, reserved_section_keys, section_model
from untaped.runtime.settings import RESERVED_SECTIONS, plugins_root
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
from untaped.sdk.storage.installed_code import distribution_code

if TYPE_CHECKING:
    from packaging.requirements import Requirement

    from untaped.runtime.composed import ComposedPlugin
    from untaped.runtime.composition import PluginEntry
    from untaped.runtime.mount import Mount
    from untaped.runtime.plugin_imports import PluginImports
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
    ``caches`` (:func:`untaped.runtime.section_models.section_model`). So does the
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
        "import-failed",
        "unmet-requirement",
        "uses-missing",
        "not-a-plugin-class",
        "bad-commands",
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

    ``name`` is the candidate's entry-point (plugin) name. ``detail`` says
    why, ending with ``fix`` when there is one: what to do about it, which a
    command run under the plugin's name repeats.
    """

    name: str
    distribution: str
    entry_point: str
    reason: str
    detail: str
    fix: str = ""

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
    #: Each deprecated name a plugin uses, by plugin name, found by this composition: empty
    #: when it was read from core's declaration cache, so only the run composing warns.
    found_uses: tuple[tuple[str, UseDecl], ...] = field(default=(), compare=False)


def candidate_distribution(candidate: PluginCandidate) -> str:
    """The distribution a candidate is reported under; a blank one is ``unknown``."""
    return candidate.distribution.strip() or "unknown"


class _Quarantine(Exception):
    """Internal control flow: one plugin failed validation.

    :func:`compose` converts it to a :class:`QuarantineRecord`.
    """

    def __init__(
        self, reason: str, detail: str, entry_point: str | None = None, *, fix: str = ""
    ) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail
        self.entry_point = entry_point
        self.fix = fix
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
            fix=self.fix,
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


def _check_requirements(candidate: PluginCandidate, state: _CompositionState) -> None:
    """Quarantine ``candidate`` when what its distribution requires isn't installed as asked.

    Metadata only, before any import: a malformed or unevaluable entry is
    ``bad-metadata``; a requirement the install doesn't meet is
    ``unmet-requirement``, its detail ending with the fix.
    """
    if not candidate.requires_dist:
        return
    try:
        found = unmet(
            candidate.distribution,
            candidate.distribution_version,
            candidate.requires_dist,
            state.installed,
        )
    except Malformed as malformed:
        raise _Quarantine(
            "bad-metadata",
            f"malformed Requires-Dist entry {malformed.args[0]!r} of distribution "
            f"{candidate.distribution!r}",
        ) from None
    if found is not None:
        raise _Quarantine("unmet-requirement", found.detail, fix=found.fix)
    if state.sdk_version is None and plain_requirements(
        candidate.requires_dist, _CORE_DISTRIBUTION
    ):
        raise _Quarantine(
            "bad-metadata",
            f"could not resolve running SDK version to check Requires-Dist "
            f"of distribution {candidate.distribution!r}",
        )


def _requiring_disabled(
    candidate: PluginCandidate, disabled: Mapping[str, QuarantineRecord]
) -> _Quarantine | None:
    """The quarantine of ``candidate`` when a plugin distribution it requires is disabled.

    A requirement its marker leaves out here is not read, as :func:`unmet` reads none.
    """
    own = canonicalize_name(candidate.distribution)
    for line in candidate.requires_dist:
        parsed = parse_requirement(line)
        if parsed is None or not is_plain(parsed) or not _applies(parsed):
            continue
        name = canonicalize_name(parsed.name)
        record = disabled.get(name)
        if record is None or name == own:
            continue
        who = f"{candidate.distribution} {candidate.distribution_version}".strip()
        fix = _first(record)
        return _Quarantine(
            "unmet-requirement",
            f"{who} requires {record.distribution}, which is disabled [{record.reason}]; {fix}",
            fix=fix,
        )
    return None


def _first(record: QuarantineRecord) -> str:
    """What to fix first when ``record``'s plugin is disabled: its own fix, or its root's.

    A plugin disabled because one it requires is disabled already names the
    plugin to fix first, so its dependents pass that on instead of nesting it.
    """
    if _FIX_FIRST.fullmatch(record.fix):
        return record.fix
    fix = f"fix {record.distribution} first"
    return f"{fix}: {record.fix}" if record.fix else fix


#: A cascaded quarantine's fix, as :func:`_first` words it.
_FIX_FIRST = re.compile(r"fix \S+ first(: .*)?", re.DOTALL)


def _applies(requirement: Requirement) -> bool:
    """Whether ``requirement``'s marker holds here; one that can't be evaluated doesn't."""
    try:
        return applies(requirement)
    except UNEVALUABLE:
        return False


def _disabled(
    ordered: Sequence[PluginCandidate], quarantined: Mapping[int, QuarantineRecord]
) -> dict[str, QuarantineRecord]:
    """Each distribution with a quarantined plugin, by canonical name, with one of its records.

    Core's own distribution is never one: core always composes, whatever a
    candidate shipped from it does.
    """
    disabled: dict[str, QuarantineRecord] = {
        canonicalize_name(ordered[index].distribution): record
        for index, record in sorted(quarantined.items())
    }
    disabled.pop(canonicalize_name(_CORE_DISTRIBUTION), None)
    return disabled


def _spread(
    ordered: Sequence[PluginCandidate],
    quarantined: dict[int, QuarantineRecord],
    live: Iterable[int],
) -> set[int]:
    """Quarantine each of ``live`` that requires a disabled plugin, until nothing changes.

    Returns the ones it quarantined. A plugin whose own import failed is told
    as the cascade when a plugin it requires is disabled, since that is
    usually why its import failed.
    """
    spread: set[int] = set()
    waiting = [index for index in live if index not in quarantined]
    changed = True
    while changed:
        changed = False
        disabled = _disabled(ordered, quarantined)
        for index in list(waiting):
            found = _requiring_disabled(ordered[index], disabled)
            if found is not None:
                quarantined[index] = found.to_record(ordered[index])
                spread.add(index)
                waiting.remove(index)
                changed = True
    disabled = _disabled(ordered, quarantined)
    for index, record in list(quarantined.items()):
        if record.reason == "import-failed":
            found = _requiring_disabled(ordered[index], disabled)
            if found is not None:
                quarantined[index] = found.to_record(ordered[index])
    return spread


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

    def __init__(self, core: PluginDecl, candidates: Sequence[PluginCandidate] = ()) -> None:
        self._candidates = tuple(candidates)
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

    @cached_property
    def installed(self) -> Installed:
        """What requirements are checked against: untaped's and each plugin's version.

        Read once per composition when first needed; untaped's version is
        resolved only then (:func:`_check_requirements` refuses a requirement
        on it when it can't be).
        """
        versions = {
            str(canonicalize_name(each.distribution)): each.distribution_version
            for each in self._candidates
            if each.distribution.strip()
        }
        versions[_CORE_DISTRIBUTION] = self.sdk_version or ""
        return Installed(versions=versions, present=functools.cache(distribution_present))

    @cached_property
    def owners(self) -> dict[str, Owner]:
        """Who each package a plugin may import from belongs to: untaped, or a plugin."""
        return owners_of(self._candidates, _CORE_DISTRIBUTION)


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


def build_plugin_app(plugin: ComposedPlugin) -> App | QuarantineRecord:
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
            "bad-commands",
            f"{type(exc).__name__}: {exc}",
        )


def resolve_target(target: object) -> object:
    """The object an entry point's ``module:attr`` target names (a non-string passes through)."""
    if not isinstance(target, str):
        return target
    module_name, sep, attr_path = target.partition(":")
    if not sep or not module_name.strip() or not attr_path.strip():
        raise _MalformedTarget(f"malformed entry-point target {target!r}: expected 'module:attr'")
    module = import_module(module_name)
    resolved: object = module
    for part in attr_path.split("."):
        try:
            resolved = getattr(resolved, part)
        except AttributeError as exc:
            raise _MalformedTarget(str(exc)) from exc
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
    dispatch and in ``untaped doctor`` (:func:`build_plugin_app`), never
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
        with _reading_plugins():
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


#: What a start shows while it imports every plugin class to keep their declaration.
READING_PLUGINS = "Reading the installed plugins"


@contextmanager
def _reading_plugins() -> Iterator[None]:
    """A progress task while every plugin class is imported; off a terminal, none.

    A start with no declaration kept (the first after an install, an upgrade
    or a plugin change) imports every plugin and every command it declares,
    then compiles their bytecode: seconds of silence that look hung. A
    ``--quiet`` or ``--verbose`` after the command word is only read by the
    declaration being built, so off a terminal the task stays silent rather
    than print a progress line a ``-q`` asked to mute. On a terminal that
    ``-q`` still sees the spinner, which clears when composition ends.
    """
    from untaped.runtime.render import stream_is_tty  # noqa: PLC0415 - only a fill reads it
    from untaped.runtime.run_output import current_sink  # noqa: PLC0415 - as above

    with current_sink().progress(READING_PLUGINS, quiet=not stream_is_tty(sys.stderr)):
        yield


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


class _MalformedTarget(ImportError):
    """An entry point's target that names nothing: not ``module:attr``, or a missing attribute."""


class _StaleDeclaration(Exception):
    """The cached declaration does not account for a candidate: compose without it."""


def _compose(
    ordered: Sequence[PluginCandidate],
    declared: Declaration | None = None,
) -> CompositionResult:
    """:func:`compose` over ``ordered``; with ``declared``, its plugin classes come from it."""
    core = _core(declared)
    state = _CompositionState(core, ordered)
    quarantined: dict[int, QuarantineRecord] = {}
    provided: dict[int, type[Plugin]] = {}
    replayed: dict[int, PluginDecl] = {}
    classes: set[int] = set()  # the plugin classes, as :func:`_refused` reads them
    contested = _replay(ordered, declared, replayed, quarantined)
    # Those the declaration doesn't account for: refused before the mount, so checked
    # again at each start; one that is now a plugin class makes the declaration stale.
    waiting = [i for i in range(len(ordered)) if i not in replayed and i not in quarantined]
    for index in waiting:  # metadata only, before any plugin's import
        try:
            _check_metadata(ordered[index], state)
        except _Quarantine as failed:
            quarantined[index] = failed.to_record(ordered[index])
    _spread(ordered, quarantined, waiting)
    uses = _provided(
        ordered, waiting, state, quarantined, provided, classes, fresh=declared is None
    )
    for index in _spread(ordered, quarantined, provided):
        del provided[index]
    if declared is not None and (classes or provided):
        raise _StaleDeclaration(ordered[min(classes | provided.keys())].name)
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
    found: tuple[tuple[str, UseDecl], ...] = ()
    if declared is None:
        # Only a class the mount composed leaves it; one refused before it never got there.
        dropped = {ordered[index].name for index in classes & quarantined.keys() & provided.keys()}
        mount = mount.kept(_refused(ordered, classes, quarantined, mount), dropped=dropped)
        kept = {
            ordered[index].name: tuple(found_here)
            for index, found_here in sorted(uses.items())
            if found_here and index in claims
        }
        lockstep = frozenset(
            ordered[index].name for index in claims if _lockstep(ordered[index], state)
        )
        mount = mount.with_uses(kept, lockstep)
        found = tuple((name, use) for name, found_here in kept.items() for use in found_here)
    plugins.insert(0, _root_plugin(mount, core))
    return CompositionResult(
        plugins=tuple(plugins),
        quarantine=tuple(quarantined[index] for index in sorted(quarantined)),
        candidates=tuple(ordered),
        mount=mount,
        taken={**state.taken, **state.core},
        found_uses=found,
    )


def _provided(
    ordered: Sequence[PluginCandidate],
    waiting: Sequence[int],
    state: _CompositionState,
    quarantined: dict[int, QuarantineRecord],
    provided: dict[int, type[Plugin]],
    classes: set[int],
    *,
    fresh: bool,
) -> dict[int, list[UseDecl]]:
    """Import each waiting candidate's plugin class into ``provided``; the deprecated names used.

    A ``fresh`` composition first looks up what the candidate imports (a kept
    declaration already holds every verdict). A refused plugin class goes
    into ``classes``, which the declaration remembers.
    """
    uses: dict[int, list[UseDecl]] = {}
    for index in waiting:
        if index in quarantined:
            continue
        try:
            if fresh:
                uses[index] = _imported_uses(ordered, index, state, quarantined)
            provided[index] = _provide(ordered[index], state)
        except _Quarantine as failed:
            quarantined[index] = failed.to_record(ordered[index])
            if failed.plugin_class:
                classes.add(index)
            continue
        if index in uses:
            uses[index].extend(_filled_uses(ordered[index], provided[index], state))
    return uses


def _imported_uses(
    ordered: Sequence[PluginCandidate],
    index: int,
    state: _CompositionState,
    quarantined: Mapping[int, QuarantineRecord],
) -> list[UseDecl]:
    """The deprecated names candidate ``index`` imports; quarantines it when one is missing.

    Read from its source before it is imported (core's ``imports`` cache keeps
    what it read), and looked up in untaped and the plugins composed so far.
    A missing name is ``uses-missing``, naming the file and line; a name of a
    disabled plugin disables it too. The declaration remembers either verdict,
    so a start reading it never looks again.
    """
    candidate = ordered[index]
    if _lockstep(candidate, state):
        return []
    disabled = _disabled(ordered, quarantined)
    verdict = look_up(_imports_of(candidate), state.owners, disabled)
    who = f"{candidate.distribution} {candidate.distribution_version}".strip()
    failed: _Quarantine | None = None
    if verdict.cascade is not None:
        record, owner = verdict.cascade.record, verdict.cascade.owner
        where = f"{record.file}:{record.line} imports {record.spelled}"
        disabled_record = disabled.get(str(canonicalize_name(owner.distribution)))
        if disabled_record is not None:
            fix = _first(disabled_record)
            failed = _Quarantine(
                "unmet-requirement",
                f"{who}: {where} from {owner.distribution}, which is disabled "
                f"[{disabled_record.reason}]; {fix}",
                fix=fix,
            )
        else:
            fix = f"fix {owner.distribution} first"
            failed = _Quarantine(
                "import-failed",
                f"{who}: {where}, and importing it raised {verdict.cascade.error}; {fix}",
                fix=fix,
            )
    elif verdict.missing:
        first = verdict.missing[0]
        detail, fix = missing_words(
            first,
            distribution=candidate.distribution,
            requires_dist=candidate.requires_dist,
            installed=state.installed.versions.get(
                str(canonicalize_name(first.owner.distribution)), ""
            ),
        )
        more = len(verdict.missing) - 1
        if more:
            detail = f"{detail} (and {more} more missing)"
        failed = _Quarantine("uses-missing", f"{who}: {detail}; {fix}", fix=fix)
    if failed is not None:
        failed.plugin_class = True  # the declaration remembers it, as a refused class
        raise failed
    return verdict.uses


def _filled_uses(
    candidate: PluginCandidate, cls: type[Plugin], state: _CompositionState
) -> list[UseDecl]:
    """The deprecated contract methods ``cls``'s providers fill."""
    providers = tuple(getattr(cls, "fills", ()) or ())
    if not providers or _lockstep(candidate, state):
        return []
    return filled_uses(providers, state.owners)


def _lockstep(candidate: PluginCandidate, state: _CompositionState) -> bool:
    """Whether ``candidate`` was released and tested with what it imports, so reading finds nothing.

    That is untaped's own distribution, and one that pins every requirement it
    has on untaped or a plugin to its own version. A candidate given as an
    object has no source to read either.
    """
    if not isinstance(candidate.target, str):
        return True
    if canonicalize_name(candidate.distribution) == canonicalize_name(_CORE_DISTRIBUTION):
        return True
    pinned = False
    for line in candidate.requires_dist:
        parsed = parse_requirement(line)
        if parsed is None or not is_plain(parsed) or not _applies(parsed):
            continue
        if str(canonicalize_name(parsed.name)) not in state.installed.versions:
            continue
        specifiers = list(parsed.specifier)
        if not (
            len(specifiers) == 1
            and is_exact(specifiers[0])
            and specifiers[0].version == candidate.distribution_version
        ):
            return False
        pinned = True
    return pinned


def _imports_of(candidate: PluginCandidate) -> tuple[ImportRecord, ...]:
    """What ``candidate``'s code imports, from core's ``imports`` cache or read now.

    Keyed by the candidate's own code, so an edit to it (or a reinstall)
    reads it again and installing another plugin doesn't.
    """
    from untaped.runtime.plugin_package import candidate_package  # noqa: PLC0415 - composing

    module = str(candidate.target).partition(":")[0]
    package = candidate_package(candidate.target)

    def fill(_key: str, _progress: ProgressHandle) -> Filled[PluginImports]:
        return Filled(value=read_imports(module, package))

    try:
        from untaped.core.settings import IMPORTS  # noqa: PLC0415 - core imports the runtime

        code = distribution_code(importlib_metadata.distribution(candidate.distribution))
        # one distribution may hold several plugins; the stdlib's names are the Python's
        python = f"py{sys.version_info.major}{sys.version_info.minor}"
        key = f"{_key_word(candidate.distribution)}.{_key_word(module)}.{python}.h{code[:24]}"
        served = IMPORTS.serve({key: f"{candidate.distribution} imports"}, refresh=None, fill=fill)
        return served.value[key].records
    except Exception:  # no metadata to key by, or the cache failed: read, as before it existed
        _LOG.debug("reading %s's imports without the cache", candidate.distribution, exc_info=True)
        return read_imports(module, package).records


def _key_word(distribution: str) -> str:
    """A distribution's name as a cache key word: ``untaped_acme``."""
    word = re.sub(r"[^a-z0-9_]", "_", str(canonicalize_name(distribution)))
    return word if word[:1].isalpha() else f"d{word}"


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
            quarantined[index] = _Quarantine(found.reason, found.detail, fix=found.fix).to_record(
                candidate
            )
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
                fix=record.fix,
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
    from untaped.runtime.composition import PluginEntry  # noqa: PLC0415 - plugin classes only
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
        for index in _spread(ordered, quarantined, mounted):
            del mounted[index]
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
    """Whether ``obj`` is a plugin class, without importing the plugin interface.

    A plugin class subclasses :class:`untaped.sdk.plugin.Plugin`, so its import
    loaded that module; an entry point naming anything else never pays for it.
    """
    module = sys.modules.get("untaped.sdk.plugin")
    return module is not None and isinstance(obj, type) and issubclass(obj, module.Plugin)


def _composed(mount: Mount, name: str, plugin_ref: PluginRef) -> ComposedPlugin:
    """The composed plugin ``name`` of ``mount``, as core's readers read it."""
    from untaped.runtime.composed import ComposedPlugin  # noqa: PLC0415 - imports the SDK blocks

    return ComposedPlugin.of(mount, name, plugin_ref)


def _entry_target(candidate: PluginCandidate, cls: type[Plugin]) -> str:
    """The ``module:Qualname`` an entry point names: its target, or the class given directly."""
    from untaped.runtime.declaration import target_of  # noqa: PLC0415 - plugin classes only

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


def _check_metadata(candidate: PluginCandidate, state: _CompositionState) -> None:
    """The gates read from distribution metadata alone, before any plugin code runs.

    Raises :class:`_Quarantine` on the first failed check.
    """
    _check_entry_point_group(candidate)
    _check_entry_point_name(candidate)
    _check_requirements(candidate, state)


def _provide(candidate: PluginCandidate, state: _CompositionState) -> type[Plugin]:
    """Resolve one candidate's entry point to its plugin class, its metadata already checked.

    The entry point names the class (``untaped_acme.plugin:Acme``), which
    composition reads with the other plugin classes (:func:`_mounted`);
    anything else (an object, a function) is ``not-a-plugin-class`` and is
    never called. A target that names nothing (not ``module:attr``, a module
    or attribute that isn't there) is ``malformed-entry-point``; anything
    raised while importing the plugin's module is ``import-failed``.

    Raises :class:`_Quarantine` on the first failed check.
    """
    try:
        found = resolve_target(candidate.target)
    except Exception as exc:
        if _names_nothing(candidate.target, exc):
            raise _Quarantine(
                "malformed-entry-point",
                f"could not resolve entry point {candidate.target!r} of "
                f"distribution {candidate.distribution!r}: {exc}",
                entry_point="",
            ) from None
        missing = _missed_loading(candidate, exc, state)
        if missing is not None:
            raise missing from None
        raise _Quarantine(
            "import-failed",
            f"importing entry point {candidate.target!r} of distribution "
            f"{candidate.distribution!r} raised {type(exc).__name__}: {exc}",
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
        f"names {_describe(found)}, not a plugin class; untaped composes plugin classes: "
        f"name the class ('{_package_of(candidate)}.plugin:{_class_name(candidate)}')",
    )


def _missed_loading(
    candidate: PluginCandidate, exc: Exception, state: _CompositionState
) -> _Quarantine | None:
    """``uses-missing`` when loading the plugin's code reached a name its owner doesn't have.

    The startup reader finds the names a plugin imports; one looked up while
    its modules load (``api.Volume`` at module level, an ``import_module``)
    shows only here. Worded by the floor rule, as the reader's verdict is.
    """
    from untaped.runtime.missing_names import missed  # noqa: PLC0415 - on a failed import only
    from untaped.runtime.plugin_package import candidate_package  # noqa: PLC0415
    from untaped.runtime.plugin_uses import floor_rule, owner_of  # noqa: PLC0415

    package = candidate_package(candidate.target)
    if package is None or _lockstep(candidate, state):
        return None
    own = {package: candidate}
    found = missed(exc, state.owners, lambda module: owner_of(module, own) is not None)
    if found is None or canonicalize_name(found.owner.distribution) == canonicalize_name(
        candidate.distribution
    ):
        return None
    installed = state.installed.versions.get(str(canonicalize_name(found.owner.distribution)), "")
    rule = floor_rule(
        distribution=candidate.distribution,
        requires_dist=candidate.requires_dist,
        owner=found.owner.distribution,
        installed=installed,
    )
    have = f"{found.owner.distribution} {installed}".strip()
    who = f"{candidate.distribution} {candidate.distribution_version}".strip()
    where = f"{_source_of(found.module, found.file)}:{found.line}"
    return _Quarantine(
        "uses-missing",
        f"{who}: {where} uses {found.spelled}, which {have} doesn't have{rule.clause}; {rule.fix}",
        entry_point="",
        fix=rule.fix,
    )


def _source_of(module: str, file: str) -> str:
    """``module``'s file as the reader's records say it: from the folder holding its package."""
    tail = "/__init__.py" if PurePath(file).name == "__init__.py" else ".py"
    return module.replace(".", "/") + tail


def _names_nothing(target: object, exc: Exception) -> bool:
    """Whether resolving ``target`` failed because it names nothing, not because its code raised.

    That is a target that isn't ``module:attr``, a module of it that isn't
    installed, or an attribute its module doesn't have.
    """
    if isinstance(exc, _MalformedTarget):
        return True
    module = target.partition(":")[0] if isinstance(target, str) else ""
    return (
        isinstance(exc, ModuleNotFoundError)
        and exc.name is not None
        and (module == exc.name or module.startswith(f"{exc.name}."))
    )


def _check_distribution(candidate: PluginCandidate) -> None:
    if not candidate.distribution.strip():
        raise _Quarantine(
            "bad-metadata",
            f"plugin {candidate.name!r} declares an empty distribution name",
        )
