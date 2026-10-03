"""Internal capability composition kernel (spec §§1-5).

Implements the provider pipeline: discovery and metadata pre-checks, provider
resolution, declaration validation, quarantine of every claimant of a contested
name or section, then app-factory staging and commit. Every capability,
first-party ones included, arrives as an entry-point candidate; every violation
yields a :class:`QuarantineRecord` while composition continues. Doctor-check
bodies never run here.

This module is intentionally NOT re-exported: provider authors import the
stable surface from :mod:`untaped.sdk` instead.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import cached_property
from importlib import import_module
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Protocol

from cyclopts import App
from packaging.markers import UndefinedEnvironmentName
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion
from pydantic import BaseModel

from untaped.errors import ConfigError
from untaped.settings import (
    RESERVED_STATE_SECTIONS,
    Settings,
    validate_disjoint_settings_sections,
)

#: Distribution whose version ``Requires-Dist: untaped`` is checked against.
_CORE_DISTRIBUTION = "untaped"

#: Entry-point group every capability is discovered from (spec §7.2).
CAPABILITIES_ENTRY_POINT_GROUP = "untaped.capabilities"

#: Reserved root command/layout names no capability may claim (spec §5 row 1).
_RESERVED_COMMAND_ROOTS = RESERVED_STATE_SECTIONS | {
    "config",
    "profile",
    "skills",
    "doctor",
    "capabilities",
    "setup",
    "alias",
}


class CapabilityProvider(Protocol):
    """Entry-point contract: a nullary callable returning a ``CapabilitySpec``."""

    def __call__(self) -> CapabilitySpec: ...


@dataclass(frozen=True)
class SkillAsset:
    """A packaged agent skill shipped by a capability (spec §3)."""

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
    """A health check contributed by the shell or a capability (spec §3).

    An ``online`` check contacts a remote service, so only
    ``untaped doctor --online`` runs it; every other check stays offline.
    """

    id: str
    title: str
    run: Callable[[CapabilityContext], DoctorResult]
    online: bool = False


@dataclass(frozen=True)
class DoctorResult:
    """Outcome of one doctor-check body (spec §3).

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
class CapabilityContext:
    """Frozen per-invocation snapshot handed to a doctor-check body (spec §3)."""

    capability: str
    config_section: str
    profile_fields: frozenset[str]
    state_fields: frozenset[str]
    settings: BaseModel | None


def _check_spec_shape(
    name: str,
    config_section: str,
    profile_model: object,
    state_model: object,
    label: str,
) -> None:
    if not name.strip():
        raise ConfigError(f"{label} name must not be empty")
    if not config_section.strip():
        raise ConfigError(f"{label} config_section must not be empty")
    if not (isinstance(profile_model, type) and issubclass(profile_model, BaseModel)):
        raise ConfigError(f"{label} profile_model must be a pydantic BaseModel subclass")
    if state_model is not None and not (
        isinstance(state_model, type) and issubclass(state_model, BaseModel)
    ):
        raise ConfigError(f"{label} state_model must be a pydantic BaseModel subclass or None")


@dataclass(frozen=True)
class ApplicationSpec:
    """The root application."""

    name: str
    app_factory: Callable[[], App]
    config_section: str
    profile_model: type[BaseModel]
    state_model: type[BaseModel] | None = None
    skills: tuple[SkillAsset, ...] = ()
    doctor_checks: tuple[DoctorCheck, ...] = ()

    def __post_init__(self) -> None:
        _check_spec_shape(
            self.name, self.config_section, self.profile_model, self.state_model, "shell"
        )
        object.__setattr__(self, "skills", tuple(self.skills))
        object.__setattr__(self, "doctor_checks", tuple(self.doctor_checks))


@dataclass(frozen=True)
class CapabilitySpec:
    """One composable capability unit (spec §1).

    ``help`` is the one-line summary shown in the root command listing. A
    capability that declares it is mounted lazily: its ``app_factory`` (and
    so its CLI import tree) runs only when the command is dispatched. Without
    it, the factory runs during composition and the listing falls back to
    the built app's own help.
    """

    name: str
    app_factory: Callable[[], App]
    config_section: str
    profile_model: type[BaseModel]
    state_model: type[BaseModel] | None = None
    skills: tuple[SkillAsset, ...] = ()
    doctor_checks: tuple[DoctorCheck, ...] = ()
    help: str | None = None

    def __post_init__(self) -> None:
        _check_spec_shape(
            self.name,
            self.config_section,
            self.profile_model,
            self.state_model,
            f"capability {self.name!r}",
        )
        if self.help is not None and (
            not isinstance(self.help, str) or not self.help.strip() or "\n" in self.help
        ):
            raise ConfigError(
                f"capability {self.name!r} help must be a non-empty single line or None"
            )
        object.__setattr__(self, "skills", tuple(self.skills))
        object.__setattr__(self, "doctor_checks", tuple(self.doctor_checks))


@dataclass(frozen=True)
class ProviderRef:
    """How a composed capability arrived (spec §3)."""

    distribution: str
    entry_point: str


@dataclass(frozen=True)
class RegisteredCapability:
    """A fully validated, committed capability (spec §3)."""

    spec: CapabilitySpec
    provider_ref: ProviderRef
    skills: tuple[SkillAsset, ...]
    #: App staged by the one validating ``app_factory`` call, reused at
    #: mount time; ``None`` exactly when the spec sets ``help`` (deferred).
    app: App | None = None


#: Every valid quarantine/diagnostic reason code lives here (spec §5 table).
VALID_REASONS = frozenset(
    {
        "reserved-root",
        "duplicate-name",
        "duplicate-section",
        "profile-state-overlap",
        "state-shadow",
        "duplicate-skill",
        "bad-skill-asset",
        "duplicate-doctor-check",
        "doctor-check-failed",
        "malformed-entry-point",
        "bad-app-factory",
        "bad-metadata",
    }
)


@dataclass(frozen=True)
class QuarantineRecord:
    """Why a provider was excluded (spec §3).

    ``name`` is the candidate's entry-point (capability) name.
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
    provider code (spec §7.2); the §7.3 listing reports
    ``distribution_version`` for candidates.
    """

    distribution: str
    name: str
    target: object
    distribution_version: str = ""
    entry_point_group: str = CAPABILITIES_ENTRY_POINT_GROUP
    requires_dist: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "requires_dist", tuple(self.requires_dist))


@dataclass(frozen=True)
class CompositionResult:
    """Committed capabilities plus quarantine records for one composition."""

    capabilities: tuple[RegisteredCapability, ...] = ()
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
    if candidate.entry_point_group != CAPABILITIES_ENTRY_POINT_GROUP:
        raise _Quarantine(
            "bad-metadata",
            f"entry-point group {candidate.entry_point_group!r} of distribution "
            f"{candidate.distribution!r} is not the capabilities group "
            f"{CAPABILITIES_ENTRY_POINT_GROUP!r}",
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


def discover_candidates(
    *, group: str = CAPABILITIES_ENTRY_POINT_GROUP
) -> tuple[ProviderCandidate, ...]:
    """Discover every capability candidate from entry points (spec §7.2).

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


def _is_reserved(value: str) -> bool:
    return value in Settings.model_fields or value in _RESERVED_COMMAND_ROOTS


def _check_reserved_and_names(spec: CapabilitySpec, state: _CompositionState) -> None:
    if _is_reserved(spec.name):
        raise _Quarantine("reserved-root", f"reserved capability name: {spec.name!r}")
    if _is_reserved(spec.config_section):
        raise _Quarantine("reserved-root", f"reserved config section: {spec.config_section!r}")
    if spec.name == state.shell.name:
        raise _Quarantine(
            "duplicate-name",
            f"duplicate capability name: {spec.name!r} (already provided by the shell)",
        )


def _check_duplicate_section(spec: CapabilitySpec, state: _CompositionState) -> None:
    if spec.config_section == state.shell.config_section:
        raise _Quarantine(
            "duplicate-section",
            f"duplicate config section: {spec.config_section!r} (already provided by the shell)",
        )


def _check_state_model(spec: CapabilitySpec, state: _CompositionState) -> None:
    if spec.state_model is None:
        return
    try:
        validate_disjoint_settings_sections(
            spec.config_section, spec.profile_model, spec.state_model
        )
    except ConfigError as exc:
        raise _Quarantine("profile-state-overlap", str(exc)) from None
    # Before duplicate-section so a state collision with the shell's section
    # reports the specific diagnosis rather than the generic duplicate; only
    # the shell can share a section, since candidates sharing one are contested.
    if spec.config_section != state.shell.config_section:
        return
    claimed = set(state.shell.profile_model.model_fields)
    shadowed = sorted(set(spec.state_model.model_fields) & claimed)
    if shadowed:
        joined = ", ".join(shadowed)
        raise _Quarantine(
            "state-shadow",
            f"state fields shadow profile fields of section {spec.config_section!r}: {joined}",
        )


def _check_skills(spec: CapabilitySpec, state: _CompositionState) -> None:
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
                f"malformed skill asset at index {index} of capability {spec.name!r}: {skill!r}",
            )
        if skill.name in state.skill_names or skill.name in seen_skills:
            raise _Quarantine("duplicate-skill", f"duplicate skill name: {skill.name!r}")
        seen_skills.add(skill.name)


def _check_doctor_checks(spec: CapabilitySpec, state: _CompositionState) -> None:
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
                f"malformed doctor check of capability {spec.name!r}: {check!r}",
            )
        if check.id in state.doctor_ids or check.id in seen_checks:
            raise _Quarantine("duplicate-doctor-check", f"duplicate doctor id: {check.id!r}")
        seen_checks.add(check.id)


def _check_rows_1_to_8(spec: CapabilitySpec, state: _CompositionState) -> None:
    _check_reserved_and_names(spec, state)
    _check_state_model(spec, state)
    _check_duplicate_section(spec, state)
    _check_skills(spec, state)
    _check_doctor_checks(spec, state)


def _check_factory(spec: CapabilitySpec) -> App:
    try:
        staged = spec.app_factory()
    except Exception as exc:
        raise _Quarantine(
            "bad-app-factory",
            f"app factory of capability {spec.name!r} raised: {exc}",
        ) from None
    if not isinstance(staged, App):
        raise _Quarantine(
            "bad-app-factory",
            f"app factory of capability {spec.name!r} returned "
            f"{type(staged).__name__}, expected cyclopts App",
        )
    return staged


def run_deferred_factory(capability: RegisteredCapability) -> App | QuarantineRecord:
    """The capability's app, running a deferred factory; a failure as a quarantine record.

    The one place a deferred factory runs, for first dispatch and
    ``untaped doctor`` alike. An eager
    capability returns its staged app without running anything.
    """
    if capability.app is not None:
        return capability.app
    try:
        return _check_factory(capability.spec)
    except _Quarantine as failed:
        ref = capability.provider_ref
        return QuarantineRecord(
            capability.spec.name, ref.distribution, ref.entry_point, failed.reason, failed.detail
        )


def _commit(
    spec: CapabilitySpec,
    ref: ProviderRef,
    state: _CompositionState,
    app: App | None,
) -> RegisteredCapability:
    registered = RegisteredCapability(
        spec=spec, provider_ref=ref, skills=tuple(spec.skills), app=app
    )
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
    A capability name or config section that two or more of the survivors
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
    claims: dict[int, CapabilitySpec] = {}
    for index, candidate in enumerate(ordered):
        try:
            claims[index] = _provide(candidate, state)
        except _Quarantine as failed:
            quarantined[index] = failed.to_record(candidate)
    for index, contest in _contested(ordered, claims).items():
        quarantined[index] = contest.to_record(ordered[index])
        del claims[index]
    capabilities: list[RegisteredCapability] = []
    for index, spec in claims.items():
        candidate = ordered[index]
        try:
            _check_skills(spec, state)
            _check_doctor_checks(spec, state)
            staged = None if spec.help is not None else _check_factory(spec)
        except _Quarantine as failed:
            quarantined[index] = failed.to_record(candidate)
            continue
        ref = ProviderRef(
            distribution=candidate.distribution, entry_point=candidate_entry_point(candidate)
        )
        capabilities.append(_commit(spec, ref, state, staged))
    return CompositionResult(
        capabilities=tuple(capabilities),
        quarantine=tuple(quarantined[index] for index in sorted(quarantined)),
    )


def _contested(
    ordered: Sequence[ProviderCandidate], claims: dict[int, CapabilitySpec]
) -> dict[int, _Quarantine]:
    """A quarantine per claimant of a name or section shared by two or more claims.

    A claimant contesting both a name and a section gets the name's record.
    """
    contested: dict[int, _Quarantine] = {}
    for reason, kind, values in (
        ("duplicate-name", "capability name", {i: spec.name for i, spec in claims.items()}),
        (
            "duplicate-section",
            "config section",
            {i: spec.config_section for i, spec in claims.items()},
        ),
    ):
        claimants: defaultdict[str, list[int]] = defaultdict(list)
        for index, value in values.items():
            claimants[value].append(index)
        for value, indexes in claimants.items():
            if len(indexes) < 2:
                continue
            claimed_by = ", ".join(
                repr(dist) for dist in sorted(ordered[index].distribution for index in indexes)
            )
            for index in indexes:
                contested.setdefault(
                    index,
                    _Quarantine(reason, f"duplicate {kind}: {value!r} (claimed by {claimed_by})"),
                )
    return contested


def _candidate_order(candidate: ProviderCandidate) -> tuple[str, str, str]:
    return (candidate.name, candidate.distribution, candidate_entry_point(candidate))


def candidate_entry_point(candidate: ProviderCandidate) -> str:
    """The entry point a candidate's provider records carry: its target, else its name."""
    return candidate.target if isinstance(candidate.target, str) else candidate.name


def _provide(candidate: ProviderCandidate, state: _CompositionState) -> CapabilitySpec:
    """Resolve and run one candidate's provider, then validate its declaration.

    Raises :class:`_Quarantine` on the first failed check.
    """
    # Metadata-only gates precede any import: group and Requires-Dist
    # admission are decided from distribution metadata without executing
    # provider code (spec §5 Phase A).
    _check_entry_point_group(candidate)
    _check_requires_dist(candidate, state)
    try:
        provider = _resolve_target(candidate.target)
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
    except Exception as exc:
        raise _Quarantine(
            "malformed-entry-point",
            f"provider {candidate.name!r} of distribution {candidate.distribution!r} raised: {exc}",
        ) from None
    if not isinstance(spec, CapabilitySpec):
        raise _Quarantine(
            "malformed-entry-point",
            f"provider {candidate.name!r} of distribution "
            f"{candidate.distribution!r} returned {type(spec).__name__}, "
            f"expected CapabilitySpec",
        )
    _check_rows_1_to_8(spec, state)
    if candidate.name != spec.name:
        raise _Quarantine(
            "bad-metadata",
            f"entry-point name {candidate.name!r} does not match capability name {spec.name!r}",
        )
    if not candidate.distribution.strip():
        raise _Quarantine(
            "bad-metadata",
            f"provider {candidate.name!r} declares an empty distribution name",
        )
    return spec
