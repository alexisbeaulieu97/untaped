"""The folder's requirement on untaped: what the nearest ``pyproject.toml`` asks for.

Read from the project's own declaration only, the standard tables
(``[project] dependencies``, ``[project.optional-dependencies]`` and
``[dependency-groups]``), never an installer's files or environment paths.
The nearest file decides: one that names no untaped means no requirement,
however many files sit above it. A malformed file or requirement is no
requirement, so it never fails a page. The projects design (#732) adds
``.untaped/untaped-version`` as a second place to read it, here.
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from untaped.plugins.registry import parse_requirement, requirement_admits
from untaped.skills import nearest_pyproject

_UNTAPED = "untaped"


@dataclass(frozen=True)
class UntapedRequirement:
    """The requirement on untaped a project declares, and the file it is declared in."""

    file: Path
    requirement: Requirement

    def satisfied_by(self, version: str) -> bool:
        """Whether ``version`` of untaped meets it (a pre-release compared per PEP 440)."""
        return requirement_admits(self.requirement, version)


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
            return UntapedRequirement(file, parsed)
    return None


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
