"""The folder's requirement on untaped: what the nearest ``pyproject.toml`` asks for.

Read from the project's own declaration only, the standard tables
(``[project] dependencies``, ``[project.optional-dependencies]`` and
``[dependency-groups]``), never an installer's files or environment paths.
The nearest file decides: one that names no untaped means no requirement,
however many files sit above it. A malformed file or requirement is no
requirement, so it never fails a page. The walk stops at the farther of the
checkout root and the project root (:func:`~untaped.sdk.projects.nearest_pyproject`).
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from packaging.requirements import Requirement
from packaging.specifiers import InvalidSpecifier, Specifier
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from untaped.runtime.folder_layer import project_config_path
from untaped.runtime.requirements import UNEVALUABLE, parse_requirement, requirement_admits
from untaped.sdk.projects import find_root, nearest_pyproject
from untaped.sdk.words import shown_path

_UNTAPED = "untaped"
#: The first untaped that reads projects, its earliest pre-release included.
_FIRST_WITH_PROJECTS = Version("12.0.0.dev0")


@dataclass(frozen=True)
class UntapedRequirement:
    """The requirement on untaped a project declares, and the file it is declared in."""

    file: Path
    requirement: Requirement

    def satisfied_by(self, version: str) -> bool:
        """Whether ``version`` of untaped meets it (a pre-release compared per PEP 440)."""
        return requirement_admits(self.requirement, version)

    def admits_projects(self) -> bool:
        """Whether some untaped it admits reads projects (12.0 or later).

        Each specifier is checked alone: one that admits nothing from 12.0
        on rules every such version out. A marker that does not apply or a
        direct reference constrains nothing, so it admits them.
        """
        if self.requirement.url or (
            self.requirement.marker is not None
            and not self.requirement.marker.evaluate({"extra": ""})
        ):
            return True
        return all(_reaches_projects(spec) for spec in self.requirement.specifier)


def untaped_requirement(start: Path) -> UntapedRequirement | None:
    """The requirement on untaped of the nearest ``pyproject.toml`` from ``start``, if any."""
    file = nearest_pyproject(start)
    if file is None:
        return None
    try:
        data = tomllib.loads(file.read_text(encoding="utf-8"))
    except OSError, UnicodeDecodeError, tomllib.TOMLDecodeError:
        return None
    for line in _declared(data):
        parsed = parse_requirement(line)
        if parsed is not None and canonicalize_name(parsed.name) == _UNTAPED:
            return UntapedRequirement(file, parsed) if _evaluable(parsed) else None
    return None


def older_pin_note(start: Path) -> str | None:
    """A warning when the project at ``start`` has a ``config.yml`` its pin can't read.

    An untaped without projects ignores ``.untaped/config.yml``, ``profile:``
    included, so an agent pinned to one runs against the home's active profile.
    """
    root = find_root(start)
    if root is None or not project_config_path(root).is_file():
        return None
    found = untaped_requirement(start)
    if found is None or found.admits_projects():
        return None
    return (
        f"{shown_path(found.file)} pins {found.requirement}, an untaped without projects: it "
        f"ignores {shown_path(project_config_path(root))}, `profile:` included, and runs "
        "against the home's active profile. Pin untaped>=12 to read it."
    )


def _reaches_projects(spec: Specifier) -> bool:
    """Whether ``spec`` admits some version from 12.0 on (an unparsable one is assumed to)."""
    if spec.operator in {">", ">=", "!="}:
        return True
    if spec.operator in {"<", "<="}:
        return spec.contains(_FIRST_WITH_PROJECTS, prereleases=True)
    try:
        floor = Version(spec.version.removesuffix(".*"))
    except InvalidVersion:
        return True
    if spec.operator == "===":
        return floor >= _FIRST_WITH_PROJECTS
    try:
        return spec.contains(max(floor, _FIRST_WITH_PROJECTS), prereleases=True)
    except InvalidSpecifier:  # pragma: no cover - a parsed specifier takes any version
        return True


def _evaluable(requirement: Requirement) -> bool:
    """Whether its marker can be evaluated, as ``requirement_admits`` needs."""
    try:
        requirement_admits(requirement, "0")
    except UNEVALUABLE:
        return False
    return True


def _declared(data: object) -> Iterator[object]:
    """Every entry of the standard dependency tables, in table order."""
    project = _table(data, "project")
    yield from _entries(project.get("dependencies"))
    for extra in _table(project, "optional-dependencies").values():
        yield from _entries(extra)
    for group in _table(data, "dependency-groups").values():
        yield from _entries(group)


def _table(data: object, key: str) -> dict[str, object]:
    value = data.get(key) if isinstance(data, dict) else None
    return value if isinstance(value, dict) else {}


def _entries(value: object) -> list[object]:
    return value if isinstance(value, list) else []
