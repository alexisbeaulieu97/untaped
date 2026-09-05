"""Internal capability composition kernel (spec §§1–5).

Implements the four-phase provider pipeline: discovery/API pre-checks,
provider resolution, declaration validation plus app-factory staging, and
commit. Built-in violations raise :class:`ConfigError` (fatal); external
violations yield :class:`QuarantineRecord` entries while composition
continues. Doctor-check bodies never run here.

This module is intentionally NOT re-exported: provider authors import the
stable surface from :mod:`untaped.capability_api` instead.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib import import_module
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any, Protocol

from cyclopts import App
from pydantic import BaseModel

from untaped.errors import ConfigError
from untaped.settings import Settings, validate_disjoint_settings_sections

#: SDK capability-API version providers build against (spec §2).
CAPABILITY_API_VERSION: float = 1.0

#: Declared API range for built-in capabilities (spec §7.1).
_BUILTIN_API_REQUIRES: tuple[float, float] = (1.0, 2.0)

#: Distribution label used for built-in provider references (spec §7.1).
_BUILTIN_DISTRIBUTION = "untaped"

#: Entry-point group external capabilities are discovered from (spec §7.2).
CAPABILITIES_ENTRY_POINT_GROUP = "untaped.capabilities"

#: Reserved root command/layout names no capability may claim (spec §5 row 1).
_RESERVED_COMMAND_ROOTS = frozenset(
    {"profiles", "active", "config", "profile", "skills", "doctor", "capabilities"}
)


class CapabilityProvider(Protocol):
    """Entry-point contract for external capabilities (spec §2)."""

    api_requires: tuple[float, float]

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
    """A health check contributed by the shell or a capability (spec §3)."""

    id: str
    title: str
    run: Callable[[CapabilityContext], DoctorResult]


@dataclass(frozen=True)
class DoctorResult:
    """Outcome of one doctor-check body (spec §3)."""

    id: str
    ok: bool
    detail: str


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
    """The unified shell application (spec §1)."""

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
    """One composable capability unit (spec §1)."""

    name: str
    app_factory: Callable[[], App]
    config_section: str
    profile_model: type[BaseModel]
    state_model: type[BaseModel] | None = None
    skills: tuple[SkillAsset, ...] = ()
    doctor_checks: tuple[DoctorCheck, ...] = ()

    def __post_init__(self) -> None:
        _check_spec_shape(
            self.name,
            self.config_section,
            self.profile_model,
            self.state_model,
            f"capability {self.name!r}",
        )
        object.__setattr__(self, "skills", tuple(self.skills))
        object.__setattr__(self, "doctor_checks", tuple(self.doctor_checks))


@dataclass(frozen=True)
class ProviderRef:
    """How a composed capability arrived (spec §3)."""

    kind: str
    distribution: str
    entry_point: str
    api_requires: tuple[float, float]

    def __post_init__(self) -> None:
        if self.kind not in ("built-in", "external"):
            raise ConfigError(
                f"provider kind must be exactly 'built-in' or 'external', got {self.kind!r}"
            )


@dataclass(frozen=True)
class RegisteredCapability:
    """A fully validated, committed capability (spec §3)."""

    spec: CapabilitySpec
    provider_ref: ProviderRef
    skills: tuple[SkillAsset, ...]


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
        "api-range",
        "malformed-entry-point",
        "bad-app-factory",
        "bad-metadata",
    }
)


@dataclass(frozen=True)
class QuarantineRecord:
    """Why an external provider was excluded (spec §3)."""

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
class ExternalProvider:
    """One discovered external candidate awaiting composition.

    ``distribution_version``, ``entry_point_group``, and ``requires_dist``
    are captured at discovery via :mod:`importlib.metadata` without importing
    provider code (spec §7.2); the §7.3 listing reports
    ``distribution_version`` for externals.

    Rationale (Wave 1.2 round 2): the former mapping-loader ``loader_fields``
    gate was removed as YAGNI — no producer ever populated it
    (``discover_external_providers`` never set it), so its check was
    unreachable on the real path.
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


class _Quarantine(ConfigError):
    """Internal control flow: one provider failed validation.

    A ``ConfigError`` subclass so built-in failures are already fatal;
    external failures are converted to :class:`QuarantineRecord`.
    """

    def __init__(self, reason: str, detail: str, entry_point: str | None = None) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail
        self.entry_point = entry_point

    def to_record(self, candidate: ExternalProvider) -> QuarantineRecord:
        distribution = candidate.distribution.strip() or "unknown"
        if self.entry_point is not None:
            entry_point = self.entry_point
        elif isinstance(candidate.target, str):
            entry_point = candidate.target
        else:
            entry_point = candidate.name
        return QuarantineRecord(
            distribution=distribution,
            entry_point=entry_point,
            reason=self.reason,
            detail=self.detail,
        )


_MISSING: Any = object()


def check_api_range(requires: object, version: float) -> tuple[float, float]:
    """Validate an ``api_requires`` range against ``version`` (spec §5 row 10)."""
    if requires is None or requires is _MISSING:
        raise _Quarantine(
            "api-range",
            f"missing api_requires: provider declares no SDK range covering {version}",
        )
    if (
        isinstance(requires, (str, bytes))
        or not isinstance(requires, (tuple, list))
        or len(requires) != 2
    ):
        raise _Quarantine(
            "api-range",
            f"malformed api_requires {requires!r}: expected (min_inclusive, max_exclusive)",
        )
    lo, hi = requires[0], requires[1]
    for bound in (lo, hi):
        if isinstance(bound, bool) or not isinstance(bound, (int, float)):
            raise _Quarantine(
                "api-range",
                f"non-numeric api_requires {requires!r}: bounds must be finite numbers",
            )
        if not math.isfinite(bound):
            raise _Quarantine(
                "api-range",
                f"non-finite api_requires {requires!r}: bounds must be finite numbers",
            )
    lo_f, hi_f = float(lo), float(hi)
    if not lo_f < hi_f:
        raise _Quarantine(
            "api-range",
            f"inverted api_requires {(lo_f, hi_f)!r}: min_inclusive must be below max_exclusive",
        )
    if not lo_f <= version < hi_f:
        raise _Quarantine(
            "api-range",
            f"api_requires {(lo_f, hi_f)!r} does not admit SDK version {version}",
        )
    return (lo_f, hi_f)


_REQUIREMENT_NAME_RE = re.compile(
    r"^\s*([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)\s*(\[[^\]]*\])?\s*(.*?)\s*$",
    re.DOTALL,
)
_SPECIFIER_RE = re.compile(r"^(===|==|~=|!=|>=|<=|>|<)\s*(\S+)\s*$")


def _normalize_distribution_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _version_key(value: str) -> tuple[int, ...]:
    """Numeric dot-tuple for a release string (minimal PEP 440 subset)."""
    key: list[int] = []
    for chunk in value.strip().split("."):
        digits = ""
        for char in chunk:
            if char.isdigit():
                digits += char
            else:
                break
        key.append(int(digits) if digits else 0)
    return tuple(key)


def _compare_versions(left: str, right: str) -> int:
    left_key = _version_key(left)
    right_key = _version_key(right)
    width = max(len(left_key), len(right_key))
    left_key += (0,) * (width - len(left_key))
    right_key += (0,) * (width - len(right_key))
    return (left_key > right_key) - (left_key < right_key)


def _specifier_admits(operator: str, wanted: str, actual: str) -> bool:
    if operator == "===":
        return actual == wanted
    if operator == "==":
        if wanted.endswith(".*"):
            prefix = _version_key(wanted[:-2])
            return _version_key(actual)[: len(prefix)] == prefix
        return _compare_versions(actual, wanted) == 0
    if operator == "!=":
        if wanted.endswith(".*"):
            prefix = _version_key(wanted[:-2])
            return _version_key(actual)[: len(prefix)] != prefix
        return _compare_versions(actual, wanted) != 0
    if operator == ">=":
        return _compare_versions(actual, wanted) >= 0
    if operator == "<=":
        return _compare_versions(actual, wanted) <= 0
    if operator == ">":
        return _compare_versions(actual, wanted) > 0
    if operator == "<":
        return _compare_versions(actual, wanted) < 0
    if operator == "~=":
        release = wanted.split(".")
        if len(release) < 2:
            return False
        prefix = _version_key(".".join(release[:-1]))
        return _compare_versions(actual, wanted) >= 0 and (
            _version_key(actual)[: len(prefix)] == prefix
        )
    return False


def _requirement_name(requirement: object) -> str | None:
    if not isinstance(requirement, str):
        return None
    match = _REQUIREMENT_NAME_RE.match(requirement.split(";", 1)[0])
    if match is None:
        return None
    return _normalize_distribution_name(match.group(1))


def _requirement_admits(requirement: str, sdk_version: str) -> bool:
    """Whether one Requires-Dist string admits ``sdk_version``.

    Raises :class:`ValueError` when the string is not a requirement.
    """
    match = _REQUIREMENT_NAME_RE.match(requirement.split(";", 1)[0])
    if match is None:
        raise ValueError(f"malformed Requires-Dist entry {requirement!r}")
    specifiers = match.group(3)
    if not specifiers.strip():
        return True
    if specifiers.strip().startswith("@"):
        # Direct reference (PEP 508 URL): a pinned source, not a version
        # range, so admission cannot be disproved.
        return True
    for specifier in specifiers.split(","):
        part = _SPECIFIER_RE.match(specifier.strip())
        if part is None:
            raise ValueError(f"malformed Requires-Dist entry {requirement!r}")
        if not _specifier_admits(part.group(1), part.group(2), sdk_version):
            return False
    return True


def _running_sdk_version() -> str | None:
    """Running SDK version via importlib.metadata; None when unresolvable."""
    try:
        return importlib_metadata.version(_BUILTIN_DISTRIBUTION)
    except importlib_metadata.PackageNotFoundError:
        return None


def _check_entry_point_group(candidate: ExternalProvider) -> None:
    if candidate.entry_point_group != CAPABILITIES_ENTRY_POINT_GROUP:
        raise _Quarantine(
            "bad-metadata",
            f"entry-point group {candidate.entry_point_group!r} of distribution "
            f"{candidate.distribution!r} is not the capabilities group "
            f"{CAPABILITIES_ENTRY_POINT_GROUP!r}",
        )


def _check_requires_dist(candidate: ExternalProvider) -> None:
    untaped_requirements: list[str] = []
    for requirement in candidate.requires_dist:
        name = _requirement_name(requirement)
        if name is None:
            raise _Quarantine(
                "bad-metadata",
                f"malformed Requires-Dist entry {requirement!r} of distribution "
                f"{candidate.distribution!r}",
            )
        if name == _BUILTIN_DISTRIBUTION and isinstance(requirement, str):
            untaped_requirements.append(requirement)
    if not untaped_requirements:
        return
    sdk_version = _running_sdk_version()
    if sdk_version is None:
        raise _Quarantine(
            "bad-metadata",
            f"could not resolve running SDK version to check Requires-Dist "
            f"of distribution {candidate.distribution!r}",
        )
    for requirement in untaped_requirements:
        try:
            admits = _requirement_admits(requirement, sdk_version)
        except ValueError:
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


def discover_external_providers(
    *, group: str = CAPABILITIES_ENTRY_POINT_GROUP
) -> tuple[ExternalProvider, ...]:
    """Discover external candidates from entry points (spec §7.2).

    Reads distribution version, entry-point group, and Requires-Dist strings
    via :mod:`importlib.metadata` without importing any provider code.
    """
    found: list[ExternalProvider] = []
    for entry_point in importlib_metadata.entry_points(group=group):
        dist = entry_point.dist
        if dist is None:
            found.append(
                ExternalProvider(
                    distribution="unknown",
                    name=entry_point.name,
                    target=entry_point.value,
                    entry_point_group=entry_point.group,
                )
            )
            continue
        dist_name = dist.metadata.get("Name") or "unknown"
        found.append(
            ExternalProvider(
                distribution=str(dist_name),
                name=entry_point.name,
                target=entry_point.value,
                distribution_version=dist.version,
                entry_point_group=entry_point.group,
                requires_dist=tuple(dist.requires or ()),
            )
        )
    return tuple(found)


def check_builtin_metadata(ref: ProviderRef) -> None:
    """Verify the §7.1 built-in invariants; a mismatch is an SDK bug."""
    if (
        ref.kind != "built-in"
        or ref.distribution != _BUILTIN_DISTRIBUTION
        or ref.entry_point != ""
        or tuple(ref.api_requires) != _BUILTIN_API_REQUIRES
    ):
        raise _Quarantine(
            "bad-metadata",
            f"built-in provider ref breaks §7.1 invariants: {ref!r}",
        )


class _CompositionState:
    """Mutable accumulation of one composition run (shell + committed providers)."""

    def __init__(self, shell: ApplicationSpec) -> None:
        self.names: set[str] = {shell.name}
        self.sections: set[str] = {shell.config_section}
        self.profile_fields: dict[str, set[str]] = {
            shell.config_section: set(shell.profile_model.model_fields)
        }
        self.skill_names: set[str] = {skill.name for skill in shell.skills}
        self.doctor_ids: set[str] = {check.id for check in shell.doctor_checks}


def _is_reserved(value: str) -> bool:
    return value in Settings.model_fields or value in _RESERVED_COMMAND_ROOTS


def _check_rows_1_to_8(spec: CapabilitySpec, state: _CompositionState) -> None:
    if _is_reserved(spec.name):
        raise _Quarantine("reserved-root", f"reserved capability name: {spec.name!r}")
    if _is_reserved(spec.config_section):
        raise _Quarantine("reserved-root", f"reserved config section: {spec.config_section!r}")
    if spec.name in state.names:
        raise _Quarantine("duplicate-name", f"duplicate capability name: {spec.name!r}")
    if spec.state_model is not None:
        try:
            validate_disjoint_settings_sections(
                spec.config_section, spec.profile_model, spec.state_model
            )
        except ConfigError as exc:
            raise _Quarantine("profile-state-overlap", str(exc)) from None
        # Before duplicate-section so a same-section state collision reports
        # the specific diagnosis rather than the generic duplicate.
        claimed = state.profile_fields.get(spec.config_section, set())
        shadowed = sorted(set(spec.state_model.model_fields) & claimed)
        if shadowed:
            joined = ", ".join(shadowed)
            raise _Quarantine(
                "state-shadow",
                f"state fields shadow profile fields of section "
                f"{spec.config_section!r}: {joined}",
            )
    if spec.config_section in state.sections:
        raise _Quarantine(
            "duplicate-section", f"duplicate config section: {spec.config_section!r}"
        )
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
                f"malformed skill asset at index {index} of capability "
                f"{spec.name!r}: {skill!r}",
            )
        if skill.name in state.skill_names or skill.name in seen_skills:
            raise _Quarantine("duplicate-skill", f"duplicate skill name: {skill.name!r}")
        seen_skills.add(skill.name)
    seen_checks: set[str] = set()
    for check in spec.doctor_checks:
        if (
            not isinstance(check, DoctorCheck)
            or not check.id.strip()
            or not check.title.strip()
            or not callable(check.run)
        ):
            raise _Quarantine(
                "duplicate-doctor-check",
                f"malformed doctor check of capability {spec.name!r}: {check!r}",
            )
        if check.id in state.doctor_ids or check.id in seen_checks:
            raise _Quarantine("duplicate-doctor-check", f"duplicate doctor id: {check.id!r}")
        seen_checks.add(check.id)


def _check_factory(spec: CapabilitySpec) -> None:
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


def _commit(
    spec: CapabilitySpec, ref: ProviderRef, state: _CompositionState
) -> RegisteredCapability:
    registered = RegisteredCapability(
        spec=spec, provider_ref=ref, skills=tuple(spec.skills)
    )
    state.names.add(spec.name)
    state.sections.add(spec.config_section)
    state.profile_fields[spec.config_section] = set(spec.profile_model.model_fields)
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
    shell: ApplicationSpec,
    builtins: Sequence[CapabilitySpec] = (),
    externals: Sequence[ExternalProvider] = (),
) -> CompositionResult:
    """Compose the shell, built-ins, then externals; quarantine failures."""
    state = _CompositionState(shell)
    capabilities: list[RegisteredCapability] = []
    quarantined: list[QuarantineRecord] = []
    for spec in builtins:
        try:
            _check_rows_1_to_8(spec, state)
            check_api_range(_BUILTIN_API_REQUIRES, CAPABILITY_API_VERSION)
            _check_factory(spec)
            check_builtin_metadata(
                ProviderRef(
                    kind="built-in",
                    distribution=_BUILTIN_DISTRIBUTION,
                    entry_point="",
                    api_requires=_BUILTIN_API_REQUIRES,
                )
            )
        except _Quarantine as failed:
            raise ConfigError(
                f"built-in capability {spec.name!r} failed validation "
                f"[{failed.reason}]: {failed.detail}"
            ) from None
        capabilities.append(
            _commit(
                spec,
                ProviderRef(
                    kind="built-in",
                    distribution=_BUILTIN_DISTRIBUTION,
                    entry_point="",
                    api_requires=_BUILTIN_API_REQUIRES,
                ),
                state,
            )
        )
    ordered = sorted(externals, key=lambda candidate: (candidate.distribution, candidate.name))
    for candidate in ordered:
        try:
            # Metadata-only gates precede any import: group and Requires-Dist
            # admission are decided from distribution metadata without
            # executing provider code (spec §5 Phase A).
            _check_entry_point_group(candidate)
            _check_requires_dist(candidate)
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
            declared = getattr(provider, "api_requires", _MISSING)
            api_requires = check_api_range(declared, CAPABILITY_API_VERSION)
            try:
                spec = provider()
            except Exception as exc:
                raise _Quarantine(
                    "malformed-entry-point",
                    f"provider {candidate.name!r} of distribution "
                    f"{candidate.distribution!r} raised: {exc}",
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
                    f"entry-point name {candidate.name!r} does not match capability "
                    f"name {spec.name!r}",
                )
            if not candidate.distribution.strip():
                raise _Quarantine(
                    "bad-metadata",
                    f"provider {candidate.name!r} declares an empty distribution name",
                )
            _check_factory(spec)
        except _Quarantine as failed:
            quarantined.append(failed.to_record(candidate))
            continue
        capabilities.append(
            _commit(
                spec,
                ProviderRef(
                    kind="external",
                    distribution=candidate.distribution,
                    entry_point=(
                        candidate.target
                        if isinstance(candidate.target, str)
                        else candidate.name
                    ),
                    api_requires=api_requires,
                ),
                state,
            )
        )
    return CompositionResult(
        capabilities=tuple(capabilities), quarantine=tuple(quarantined)
    )
