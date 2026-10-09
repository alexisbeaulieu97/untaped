"""Internal plugin composition kernel.

Implements the provider pipeline: discovery and metadata pre-checks, provider
resolution, declaration validation, quarantine of every claimant of a contested
name, then app-factory staging and commit. Every plugin,
first-party ones included, arrives as an entry-point candidate; every violation
yields a :class:`QuarantineRecord` while composition continues. Doctor-check
bodies never run here.

This module is intentionally NOT re-exported: provider authors import the
stable surface from :mod:`untaped.sdk` instead.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cached_property
from importlib import import_module
from importlib import metadata as importlib_metadata
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING

from cyclopts import App
from packaging.markers import UndefinedEnvironmentName
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion
from pydantic import BaseModel

from untaped.deprecated_keys import key_mappings, mapping_errors
from untaped.errors import ConfigError
from untaped.records import DuplicateKindError
from untaped.settings import (
    DEFAULT_CONFIG_PATH,
    RESERVED_SECTIONS,
    validate_disjoint_settings_sections,
)
from untaped.stability import Stability, check_stability, mark_errors

if TYPE_CHECKING:
    from untaped.contracts import Contract

#: Distribution whose version ``Requires-Dist: untaped`` is checked against.
_CORE_DISTRIBUTION = "untaped"

#: Entry-point group every plugin is discovered from.
PLUGINS_ENTRY_POINT_GROUP = "untaped.plugins"

#: A plugin name: lowercase words joined by single hyphens. The name is also
#: the plugin's config section, CLI group and :func:`plugin_dir`.
PLUGIN_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")

#: Names no plugin may take, whatever they would collide with.
RESERVED_PLUGIN_NAMES = frozenset(
    {"untaped", "core", "sdk", "contracts", "plugins", "extensions", "profiles", "default", "shell"}
)

#: Management commands the root app mounts beside the plugins, in mount
#: order. ``bootstrap`` mounts exactly these.
ROOT_MANAGEMENT_COMMANDS = (
    "config",
    "profile",
    "skills",
    "doctor",
    "setup",
    "auth",
    "alias",
    "plugin",
)

#: Root commands core mounts or keeps for itself; a plugin's CLI group is its
#: name, so no plugin may be named after one.
RESERVED_COMMAND_GROUPS = frozenset({*ROOT_MANAGEMENT_COMMANDS, "rank", "contracts"})


@dataclass(frozen=True)
class SkillAsset:
    """A packaged agent skill shipped by a plugin."""

    name: str
    source: Path
    description: str

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ConfigError("skill asset name must not be empty")
        if not self.description.strip():
            raise ConfigError(f"skill asset {self.name!r} must have a description")


@dataclass(frozen=True)
class DoctorCheck:
    """A health check contributed by the shell or a plugin.

    An ``online`` check contacts a remote service, so only
    ``untaped doctor --online`` runs it; every other check stays offline.
    """

    id: str
    title: str
    run: Callable[[PluginContext], DoctorResult]
    online: bool = False


@dataclass(frozen=True)
class DoctorResult:
    """Outcome of one doctor-check body.

    ``ok=False`` is a failed row (doctor exits 1). ``ok=True`` with
    ``warn=True`` is a ``warn`` row: worth attention (a deprecated setting,
    say) but not a failure, so doctor still exits 0. ``fix`` is the
    ``untaped`` command (without the program name) that repairs a failed or
    warned row, as a string (split like a shell would) or an argv list; a
    value the user supplies is a ``<NAME>`` placeholder. Doctor emits it as
    the row's ``fix`` argv and shows it under the row.

    ``automatic=True`` says the fix is safe to run unattended: it needs no
    ``<NAME>`` value, no terminal input and no confirmation, it is a
    declared write (``@writes``) that takes ``--format``, and it carries any
    ``--yes`` it needs itself. It also asks for and prints no secret, so an
    agent may run it. Doctor fails the row when an automatic fix is missing
    or has a placeholder.
    """

    id: str
    ok: bool
    detail: str
    warn: bool = False
    fix: str | list[str] | None = None
    automatic: bool = False


@dataclass(frozen=True)
class PluginContext:
    """Frozen per-invocation snapshot handed to a doctor-check body."""

    settings: BaseModel | None


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


@dataclass(frozen=True)
class ApplicationSpec:
    """The root application."""

    name: str
    app_factory: Callable[[], App]
    section: str
    settings: type[BaseModel]
    state: type[BaseModel] | None = None
    skills: tuple[SkillAsset, ...] = ()
    doctor_checks: tuple[DoctorCheck, ...] = ()

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.section.strip():
            raise ConfigError("shell name and section must not be empty")
        _check_model(self.settings, "settings", "shell")
        _check_model(self.state, "state", "shell")
        key_mappings(self.settings)  # a broken shell declaration is a core bug
        object.__setattr__(self, "skills", tuple(self.skills))
        object.__setattr__(self, "doctor_checks", tuple(self.doctor_checks))


def _no_contracts() -> Sequence[type[Contract]]:
    return ()


@dataclass(frozen=True)
class PluginSpec:
    """One composable plugin unit.

    ``name`` is also the plugin's config section and CLI group. Every part
    is optional: ``app_factory`` builds the commands mounted under the name
    (none when it is ``None``), ``settings`` is the settings model of the
    plugin's config section and ``state`` its ``state.yml`` model.

    ``help`` is the one-line summary shown in the root command listing. A
    plugin that declares it is mounted lazily: its ``app_factory`` (and
    so its CLI import tree) runs only when the command is dispatched. Without
    it, the factory runs during composition and the listing falls back to
    the built app's own help.

    ``stability`` marks the whole plugin ``experimental`` or
    ``deprecated(...)``. It sits here, never on the app the factory returns:
    a lazy mount reads only the spec.

    ``contracts`` returns the :class:`~untaped.contracts.Contract` classes the
    plugin owns, and ``provides`` maps an owner plugin's name to a function
    returning this plugin's providers of that owner's contracts. Both are
    functions with a local import, so nothing under them is imported until a
    contract is first asked for (``untaped.contracts``).
    """

    name: str
    app_factory: Callable[[], App] | None = None
    settings: type[BaseModel] | None = None
    state: type[BaseModel] | None = None
    skills: tuple[SkillAsset, ...] = ()
    doctor_checks: tuple[DoctorCheck, ...] = ()
    help: str | None = None
    stability: Stability | None = None
    contracts: Callable[[], Sequence[type[Contract]]] = _no_contracts
    provides: Mapping[str, Callable[[], Sequence[Contract]]] = field(
        default_factory=lambda: MappingProxyType({}), hash=False
    )

    def __post_init__(self) -> None:
        check_plugin_name(self.name)
        _check_model(self.settings, "settings", f"plugin {self.name!r}")
        _check_model(self.state, "state", f"plugin {self.name!r}")
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
        if not callable(self.contracts):
            raise ConfigError(f"plugin {self.name!r} contracts must be a function")
        for owner, providers in self.provides.items():
            check_plugin_name(owner)
            if not callable(providers):
                raise ConfigError(f"plugin {self.name!r} provides[{owner!r}] must be a function")
        object.__setattr__(self, "skills", tuple(self.skills))
        object.__setattr__(self, "doctor_checks", tuple(self.doctor_checks))
        object.__setattr__(self, "provides", MappingProxyType(dict(self.provides)))


class _NoSettings(BaseModel):
    """The settings of a plugin that declares none: no fields."""


def settings_model(spec: PluginSpec) -> type[BaseModel]:
    """``spec.settings``, or a model with no fields when the plugin has none."""
    return spec.settings or _NoSettings


def plugin_dir(spec: PluginSpec) -> Path:
    """The directory a plugin keeps its own data in: ``~/.untaped/plugins/<name>/``.

    It takes the spec rather than reading any ambient state, so it works
    from any thread. The directory is not created.
    """
    return Path(DEFAULT_CONFIG_PATH).expanduser().parent / "plugins" / spec.name


@dataclass(frozen=True)
class ProviderRef:
    """How a composed plugin arrived."""

    distribution: str
    entry_point: str


@dataclass(frozen=True)
class RegisteredPlugin:
    """A fully validated, committed plugin."""

    spec: PluginSpec
    provider_ref: ProviderRef
    skills: tuple[SkillAsset, ...]
    #: App staged by the one validating ``app_factory`` call, reused at
    #: mount time; ``None`` when the spec sets ``help`` (deferred) or has
    #: no ``app_factory``.
    app: App | None = None


#: Every valid quarantine/diagnostic reason code lives here.
VALID_REASONS = frozenset(
    {
        "reserved-name",
        "duplicate-name",
        "settings-state-overlap",
        "duplicate-skill",
        "bad-skill-asset",
        "duplicate-doctor-check",
        "doctor-check-failed",
        "malformed-entry-point",
        "bad-app-factory",
        "bad-metadata",
        "bad-settings-keys",
        "duplicate-kind",
    }
)


@dataclass(frozen=True)
class QuarantineRecord:
    """Why a provider was excluded.

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
class ProviderCandidate:
    """One discovered candidate awaiting composition.

    ``distribution_version``, ``entry_point_group``, and ``requires_dist``
    are captured at discovery via :mod:`importlib.metadata` without importing
    provider code; ``untaped plugin list`` reports ``distribution_version``
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
    """Committed plugins plus quarantine records for one composition."""

    plugins: tuple[RegisteredPlugin, ...] = ()
    quarantine: tuple[QuarantineRecord, ...] = ()


def candidate_distribution(candidate: ProviderCandidate) -> str:
    """The distribution a candidate is reported under; a blank one is ``unknown``."""
    return candidate.distribution.strip() or "unknown"


class _Quarantine(Exception):
    """Internal control flow: one provider failed validation.

    :func:`compose` converts it to a :class:`QuarantineRecord`.
    """

    def __init__(self, reason: str, detail: str, entry_point: str | None = None) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail
        self.entry_point = entry_point

    def to_record(self, candidate: ProviderCandidate) -> QuarantineRecord:
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


def _parse_requirement(requirement: object) -> Requirement | None:
    """Parse one PEP 508 Requires-Dist string; ``None`` when malformed."""
    if not isinstance(requirement, str):
        return None
    try:
        return Requirement(requirement)
    except InvalidRequirement:
        return None


def _requirement_admits(requirement: Requirement, sdk_version: str) -> bool:
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


def _running_sdk_version() -> str | None:
    """Running SDK version via importlib.metadata; None when unresolvable."""
    try:
        return importlib_metadata.version(_CORE_DISTRIBUTION)
    except importlib_metadata.PackageNotFoundError:
        return None


def _check_entry_point_group(candidate: ProviderCandidate) -> None:
    if candidate.entry_point_group != PLUGINS_ENTRY_POINT_GROUP:
        raise _Quarantine(
            "bad-metadata",
            f"entry-point group {candidate.entry_point_group!r} of distribution "
            f"{candidate.distribution!r} is not the plugins group "
            f"{PLUGINS_ENTRY_POINT_GROUP!r}",
        )


def _check_requires_dist(candidate: ProviderCandidate, state: _CompositionState) -> None:
    untaped_requirements: list[tuple[str, Requirement]] = []
    for requirement in candidate.requires_dist:
        parsed = _parse_requirement(requirement)
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
            admits = _requirement_admits(parsed, sdk_version)
        except InvalidVersion, UndefinedEnvironmentName:
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


def discover_candidates(*, group: str = PLUGINS_ENTRY_POINT_GROUP) -> tuple[ProviderCandidate, ...]:
    """Discover every plugin candidate from entry points.

    Reads distribution version, entry-point group, and Requires-Dist strings
    via :mod:`importlib.metadata` without importing any provider code.
    """
    found: list[ProviderCandidate] = []
    # Entry points of one distribution share its object; read its metadata once.
    read: dict[int, tuple[str, str, tuple[str, ...]]] = {}
    for entry_point in importlib_metadata.entry_points(group=group):
        dist = entry_point.dist
        if dist is None:
            found.append(
                ProviderCandidate(
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
            ProviderCandidate(
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
    """Mutable accumulation of one composition run (shell + committed providers)."""

    def __init__(self, shell: ApplicationSpec) -> None:
        self.shell = shell
        self.skill_names: set[str] = {skill.name for skill in shell.skills}
        self.doctor_ids: set[str] = {check.id for check in shell.doctor_checks}

    @cached_property
    def sdk_version(self) -> str | None:
        """The running SDK version, resolved once per composition when first needed."""
        return _running_sdk_version()


def _reserved_as(name: str) -> str | None:
    """What a reserved ``name`` would collide with; ``None`` when it is free."""
    if name in RESERVED_PLUGIN_NAMES:
        return "plugin name"
    if name in RESERVED_SECTIONS:
        return "config section"
    if name in RESERVED_COMMAND_GROUPS:
        return "command group"
    return None


def _check_reserved_name(spec: PluginSpec, state: _CompositionState) -> None:
    reserved = _reserved_as(spec.name)
    if reserved is not None:
        raise _Quarantine("reserved-name", f"reserved {reserved}: {spec.name!r}")
    if spec.name in (state.shell.name, state.shell.section):
        raise _Quarantine(
            "duplicate-name",
            f"duplicate plugin name: {spec.name!r} (already provided by the shell)",
        )


def _check_state_model(spec: PluginSpec) -> None:
    if spec.state is None:
        return
    marked = mark_errors(spec.state, state=True)
    if marked:
        raise _Quarantine("bad-settings-keys", f"plugin {spec.name!r}: {marked[0]}")
    if spec.settings is None:
        return
    try:
        validate_disjoint_settings_sections(spec.name, spec.settings, spec.state)
    except ConfigError as exc:
        raise _Quarantine("settings-state-overlap", str(exc)) from None


def _check_skills(spec: PluginSpec, state: _CompositionState) -> None:
    # Shape before collision: a collision needs a valid name to report.
    seen_skills: set[str] = set()
    for index, skill in enumerate(spec.skills):
        if (
            not isinstance(skill, SkillAsset)
            or not skill.name.strip()
            or not skill.description.strip()
        ):
            raise _Quarantine(
                "bad-skill-asset",
                f"malformed skill asset at index {index} of plugin {spec.name!r}: {skill!r}",
            )
        if skill.name in state.skill_names or skill.name in seen_skills:
            raise _Quarantine("duplicate-skill", f"duplicate skill name: {skill.name!r}")
        seen_skills.add(skill.name)


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


def _check_key_mappings(spec: PluginSpec) -> None:
    if spec.settings is None:
        return
    errors = mapping_errors(spec.settings)
    if errors:
        raise _Quarantine("bad-settings-keys", f"plugin {spec.name!r}: {errors[0]}")


def _check_declaration(spec: PluginSpec, state: _CompositionState) -> None:
    _check_reserved_name(spec, state)
    _check_key_mappings(spec)
    _check_state_model(spec)
    _check_skills(spec, state)
    _check_doctor_checks(spec, state)


def _check_factory(spec: PluginSpec, factory: Callable[[], App]) -> App:
    try:
        staged = factory()
    except DuplicateKindError as exc:
        raise _Quarantine("duplicate-kind", str(exc)) from None
    except Exception as exc:
        raise _Quarantine(
            "bad-app-factory",
            f"app factory of plugin {spec.name!r} raised: {exc}",
        ) from None
    if not isinstance(staged, App):
        raise _Quarantine(
            "bad-app-factory",
            f"app factory of plugin {spec.name!r} returned "
            f"{type(staged).__name__}, expected cyclopts App",
        )
    return staged


def run_deferred_factory(plugin: RegisteredPlugin) -> App | QuarantineRecord:
    """The plugin's app, running a deferred factory; a failure as a quarantine record.

    The one place a deferred factory runs, for first dispatch and
    ``untaped doctor`` alike. An eager
    plugin returns its staged app without running anything. A plugin
    without an ``app_factory`` has no app to build: asking for one is a
    caller bug and raises :class:`ConfigError`.
    """
    if plugin.app is not None:
        return plugin.app
    factory = plugin.spec.app_factory
    if factory is None:
        raise ConfigError(f"plugin {plugin.spec.name!r} has no commands to build")
    try:
        return _check_factory(plugin.spec, factory)
    except _Quarantine as failed:
        ref = plugin.provider_ref
        return QuarantineRecord(
            plugin.spec.name, ref.distribution, ref.entry_point, failed.reason, failed.detail
        )


def _commit(
    spec: PluginSpec,
    ref: ProviderRef,
    state: _CompositionState,
    app: App | None,
) -> RegisteredPlugin:
    registered = RegisteredPlugin(spec=spec, provider_ref=ref, skills=tuple(spec.skills), app=app)
    for skill in registered.skills:
        state.skill_names.add(skill.name)
    for check in spec.doctor_checks:
        state.doctor_ids.add(check.id)
    return registered


def _resolve_target(target: object) -> object:
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


def compose(
    shell: ApplicationSpec, candidates: Sequence[ProviderCandidate] = ()
) -> CompositionResult:
    """Compose the shell, then every candidate in name order; quarantine failures.

    Each candidate is first provided and validated against the shell alone.
    A plugin name that two or more of the survivors
    claim is contested: every claimant is quarantined, so the result never
    depends on candidate order. The rest commit in ``(name, distribution,
    entry point)`` order, where a skill name or doctor-check id already taken
    quarantines the later one. A spec with ``help`` is deferred: its factory
    runs at first dispatch and in ``untaped doctor`` (:func:`run_deferred_factory`).
    An eager factory runs only after the contest, so a claimant whose factory
    would fail still counts as a claimant.
    """
    state = _CompositionState(shell)
    ordered = sorted(candidates, key=_candidate_order)
    quarantined: dict[int, QuarantineRecord] = {}
    claims: dict[int, PluginSpec] = {}
    for index, candidate in enumerate(ordered):
        try:
            claims[index] = _provide(candidate, state)
        except _Quarantine as failed:
            quarantined[index] = failed.to_record(candidate)
    for index, contest in _contested(ordered, claims).items():
        quarantined[index] = contest.to_record(ordered[index])
        del claims[index]
    plugins: list[RegisteredPlugin] = []
    for index, spec in claims.items():
        candidate = ordered[index]
        try:
            _check_skills(spec, state)
            _check_doctor_checks(spec, state)
            factory = spec.app_factory
            staged = (
                None if factory is None or spec.help is not None else _check_factory(spec, factory)
            )
        except _Quarantine as failed:
            quarantined[index] = failed.to_record(candidate)
            continue
        ref = ProviderRef(
            distribution=candidate.distribution, entry_point=candidate_entry_point(candidate)
        )
        plugins.append(_commit(spec, ref, state, staged))
    return CompositionResult(
        plugins=tuple(plugins),
        quarantine=tuple(quarantined[index] for index in sorted(quarantined)),
    )


def _contested(
    ordered: Sequence[ProviderCandidate], claims: dict[int, PluginSpec]
) -> dict[int, _Quarantine]:
    """A quarantine per claimant of a plugin name two or more claims share.

    The name is also the config section and CLI group, so one contest
    covers all three.
    """
    claimants: defaultdict[str, list[int]] = defaultdict(list)
    for index, spec in claims.items():
        claimants[spec.name].append(index)
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


def _candidate_order(candidate: ProviderCandidate) -> tuple[str, str, str]:
    return (candidate.name, candidate.distribution, candidate_entry_point(candidate))


def candidate_entry_point(candidate: ProviderCandidate) -> str:
    """The entry point a candidate's provider records carry: its target, else its name."""
    return candidate.target if isinstance(candidate.target, str) else candidate.name


def _provide(candidate: ProviderCandidate, state: _CompositionState) -> PluginSpec:
    """Resolve and run one candidate's provider, then validate its declaration.

    Raises :class:`_Quarantine` on the first failed check.
    """
    # Metadata-only gates precede any import: group and Requires-Dist
    # admission are decided from distribution metadata without executing
    # provider code.
    _check_entry_point_group(candidate)
    _check_requires_dist(candidate, state)
    try:
        provider = _resolve_target(candidate.target)
    except DuplicateKindError as exc:
        raise _Quarantine("duplicate-kind", str(exc), entry_point="") from None
    except Exception as exc:
        raise _Quarantine(
            "malformed-entry-point",
            f"could not resolve entry point {candidate.target!r} of "
            f"distribution {candidate.distribution!r}: {exc}",
            entry_point="",
        ) from None
    if not callable(provider):
        raise _Quarantine(
            "malformed-entry-point",
            f"entry point {candidate.name!r} of distribution "
            f"{candidate.distribution!r} is not callable: {provider!r}",
        )
    try:
        spec = provider()
    except DuplicateKindError as exc:
        raise _Quarantine("duplicate-kind", str(exc)) from None
    except Exception as exc:
        raise _Quarantine(
            "malformed-entry-point",
            f"provider {candidate.name!r} of distribution {candidate.distribution!r} raised: {exc}",
        ) from None
    if not isinstance(spec, PluginSpec):
        raise _Quarantine(
            "malformed-entry-point",
            f"provider {candidate.name!r} of distribution "
            f"{candidate.distribution!r} returned {type(spec).__name__}, "
            f"expected PluginSpec",
        )
    _check_declaration(spec, state)
    if candidate.name != spec.name:
        raise _Quarantine(
            "bad-metadata",
            f"entry-point name {candidate.name!r} does not match plugin name {spec.name!r}",
        )
    if not candidate.distribution.strip():
        raise _Quarantine(
            "bad-metadata",
            f"provider {candidate.name!r} declares an empty distribution name",
        )
    return spec
