"""What a plugin's distribution requires, read from its metadata alone, and whether it is met.

Plugins declare floors (``untaped>=12.2``, ``untaped-git>=12.1``), never upper
limits: one stale plugin would otherwise hold back every install that has it.
Composition checks every plugin's plain requirements against what is installed
before importing anything (:func:`unmet`): versions for ``untaped`` and the
plugins, the code the declaration cache follows, and presence for every other
requirement. The parsing helpers here are what every reader of a
``Requires-Dist`` line uses.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from importlib import metadata as importlib_metadata

from packaging.markers import UndefinedComparison, UndefinedEnvironmentName
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import Specifier
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

#: What ``requirement_admits`` raises on an entry whose marker or version it cannot evaluate.
UNEVALUABLE = (InvalidVersion, UndefinedComparison, UndefinedEnvironmentName, KeyError)


def parse_requirement(requirement: object) -> Requirement | None:
    """Parse one PEP 508 Requires-Dist string; ``None`` when malformed."""
    if not isinstance(requirement, str):
        return None
    try:
        return Requirement(requirement)
    except InvalidRequirement:
        return None


def requirement_admits(requirement: Requirement, version: str) -> bool:
    """Whether a parsed Requires-Dist entry admits ``version``.

    An entry whose environment marker does not apply (e.g. an ``extra``
    nobody requested) constrains nothing. A direct reference (PEP 508 URL)
    is a pinned source, not a version range, so admission cannot be
    disproved. Pre-releases are compared per PEP 440 (``6.1.0.dev3`` does
    not satisfy ``>=6.1.0``).
    """
    if not applies(requirement):
        return True
    if requirement.url:
        return True
    return requirement.specifier.contains(version, prereleases=True)


def applies(requirement: Requirement) -> bool:
    """Whether ``requirement``'s marker holds in this environment, no extra requested.

    Raises one of :data:`UNEVALUABLE` on a marker that can't be evaluated.
    """
    return requirement.marker is None or requirement.marker.evaluate({"extra": ""})


def plain_requirements(requires_dist: Sequence[str], distribution: str) -> list[Requirement]:
    """The requirements on ``distribution`` a default install pulls in: no ``extra`` marker.

    Another marker (``python_version``, ``sys_platform``) keeps a requirement plain.
    """
    wanted = canonicalize_name(distribution)
    return [
        parsed
        for parsed in (parse_requirement(line) for line in requires_dist)
        if parsed is not None and canonicalize_name(parsed.name) == wanted and is_plain(parsed)
    ]


def is_plain(requirement: Requirement) -> bool:
    """Whether a default install pulls ``requirement`` in: its marker doesn't test ``extra``."""
    return requirement.marker is None or not mentions_extra(str(requirement.marker))


def declared_distributions(requirements: Sequence[str]) -> set[str]:
    """Canonical names of the distributions ``requirements`` (its ``Requires-Dist``) declare.

    That is what a default install pulls in: extra-guarded requirements are
    left out; other markers (``python_version``, ``sys_platform``…) are
    ignored; invalid strings are skipped.
    """
    names: set[str] = set()
    for requirement in requirements:
        parsed = parse_requirement(requirement)
        if parsed is not None and is_plain(parsed):
            names.add(canonicalize_name(parsed.name))
    return names


def mentions_extra(marker: str) -> bool:
    """Whether ``marker`` tests the ``extra`` variable (quoted values aside)."""
    return re.search(r"\bextra\b", re.sub(r"\"[^\"]*\"|'[^']*'", "", marker)) is not None


def range_text(requirement: Requirement) -> str:
    """``requirement``'s range as people write it, lower bound first: ``>=2,<3``."""
    return ",".join(
        sorted((str(each) for each in requirement.specifier), key=lambda each: each[0] != ">")
    )


def floor_for(version: str) -> str:
    """A floor at ``version``'s minor, ``>=12.1``; ``""`` when the version is unknown.

    A pre-release is its own floor (``>=12.0.0a1``), since ``>=12.0`` leaves it out.
    """
    try:
        parsed = Version(version)
    except InvalidVersion:
        return ""
    return f">={parsed.public if parsed.is_prerelease else f'{parsed.major}.{parsed.minor}'}"


def is_exact(specifier: Specifier) -> bool:
    """Whether ``specifier`` pins one release: ``==12.1.0`` or ``===12.1.0``, not ``==12.*``."""
    return specifier.operator == "===" or (
        specifier.operator == "==" and not specifier.version.endswith(".*")
    )


def is_upper(specifier: Specifier) -> bool:
    """Whether ``specifier`` excludes newer releases: ``<``, ``<=``, ``~=``, ``==12.*`` or a pin."""
    return specifier.operator in {"<", "<=", "~=", "==", "==="}


def floor_of(specifier: Specifier) -> str | None:
    """The version a specifier's lower side starts at; ``None`` when it has none (``<3``)."""
    if specifier.operator in {"<", "<=", "!="}:
        return None
    return specifier.version.removesuffix(".*")


class Malformed(Exception):
    """A requirement that doesn't parse, or whose marker can't be evaluated."""


@dataclass(frozen=True)
class Unmet:
    """Why a plugin's requirement is not met, and what to do about it."""

    facts: str
    fix: str

    @property
    def detail(self) -> str:
        """The facts then the fix, as a quarantine record says them."""
        return f"{self.facts}; {self.fix}"


@dataclass(frozen=True)
class Installed:
    """What composition compares requirements with, read once per composition.

    ``versions`` holds the distributions whose version counts, canonical name
    to version (``""`` when unknown): ``untaped`` and every plugin
    distribution. ``present`` answers for any other distribution, from its
    metadata only.
    """

    versions: Mapping[str, str]
    present: Callable[[str], bool]


def unmet(
    requirer: str, version: str, requires_dist: Sequence[str], installed: Installed
) -> Unmet | None:
    """The first of ``requires_dist``'s plain requirements ``installed`` doesn't meet.

    ``requirer`` and ``version`` name the distribution that declares them.
    Raises :class:`Malformed` on a requirement that doesn't parse or whose
    marker can't be evaluated. A requirement with an ``extra`` marker, or one
    whose marker doesn't hold here, is not read.
    """
    for line in requires_dist:
        parsed = parse_requirement(line)
        if parsed is None:
            raise Malformed(line)
        if not is_plain(parsed):
            continue
        try:
            if not applies(parsed):
                continue
        except UNEVALUABLE:
            raise Malformed(line) from None
        found = _unmet(parsed, requirer, version, installed)
        if found is not None:
            return found
    return None


def _unmet(
    requirement: Requirement, requirer: str, version: str, installed: Installed
) -> Unmet | None:
    name = str(canonicalize_name(requirement.name))
    who = f"{requirer} {version}".strip()
    wanted = f"{requirement.name}{range_text(requirement)}"
    if name not in installed.versions:
        if installed.present(name):
            return None
        return Unmet(
            f"{who} requires {wanted}, which is not installed",
            f"install {requirement.name}, or remove {requirer}",
        )
    text = installed.versions[name]
    have = _version(text)
    if requirement.url or have is None:
        return None  # a version it can't compare is read as unknown
    rejecting = next(
        (spec for spec in requirement.specifier if not spec.contains(have, prereleases=True)),
        None,
    )
    if rejecting is None:
        return None
    facts = f"{who} requires {wanted}, but {requirement.name} {text} is installed"
    return Unmet(facts, _fix(rejecting, requirement.name, have, requirer))


def _version(text: str) -> Version | None:
    """``text`` as a version; ``None`` when unknown (``""``) or not a version at all."""
    try:
        return Version(text)
    except InvalidVersion:
        return None


def _fix(rejecting: Specifier, required: str, have: Version, requirer: str) -> str:
    """What to do when ``rejecting`` refuses version ``have`` of ``required``."""
    if rejecting.operator == "!=":
        return f"install another release of {required}, or remove {requirer}"
    floor = _version(floor_of(rejecting) or "")
    below = floor is not None and (have <= floor if rejecting.operator == ">" else have < floor)
    if is_exact(rejecting):
        older, newer = (required, requirer) if below else (requirer, required)
        return f"upgrade {older} to match {newer}"
    if below:
        return f"upgrade {required}, or remove {requirer}"
    return f"upgrade {requirer}, or remove it"


def distribution_present(name: str) -> bool:
    """Whether a distribution named ``name`` is installed, read from its metadata only."""
    try:
        importlib_metadata.distribution(name)
    except importlib_metadata.PackageNotFoundError:
        return False
    return True
