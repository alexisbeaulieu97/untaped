"""Where a run's subjects come from: the plugin repository it runs in, or ``--stdin``.

Inside a plugin repository the nearest ``pyproject.toml`` (up to the project
root) names its plugins under ``[project.entry-points."untaped.plugins"]``;
each installed one is an ``InstalledPlugin`` subject, and one that is not
installed is a finding, never a subject. ``--stdin`` reads piped rows,
each one subject as its model.
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from untaped.sdk import InstalledPlugin, Record, UsageError, nearest_pyproject
from untaped_lint.providers import conventions
from untaped_lint.records import Finding, public

_GROUP = "untaped.plugins"


@dataclass
class Found:
    """The subjects of a run and the findings that kept a declared one from being one."""

    subjects: list[Record] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)


def declared_plugins(start: Path) -> list[str]:
    """The plugin names the nearest ``pyproject.toml`` from ``start`` up declares."""
    project = nearest_pyproject(start)
    return [] if project is None else _entry_points(project)


def _entry_points(project: Path) -> list[str]:
    try:
        data = tomllib.loads(project.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise UsageError(f"cannot read {project}: {exc}") from None
    group = data.get("project", {}).get("entry-points", {}).get(_GROUP, {})
    return sorted(group) if isinstance(group, dict) else []


def repository_subjects(names: Iterable[str], installed: Sequence[InstalledPlugin]) -> Found:
    """A subject per declared plugin that is installed; a finding for one that isn't."""
    by_name = {plugin.name: plugin for plugin in installed}
    found = Found()
    for name in names:
        plugin = by_name.get(name)
        if plugin is None:
            found.findings.append(not_installed(name))
        else:
            found.subjects.append(plugin)
    return found


def not_installed(name: str) -> Finding:
    """Conventions' finding for plugin ``name``, with its public id."""
    return public(conventions.KEY, conventions.not_installed(name))


def stdin_subjects(rows: Iterable[tuple[str | None, Record]]) -> list[Record]:
    """Each piped row as a subject; a kind-less one is a usage error.

    untaped's own row of ``plugin list`` (``root``) is skipped: the plugin
    check does not read untaped itself as an installed plugin yet.
    """
    subjects: list[Record] = []
    for kind, row in rows:
        if not kind:
            raise UsageError("a record on stdin has no kind; pipe untaped's --format pipe output")
        if isinstance(row, InstalledPlugin) and row.root:
            continue
        subjects.append(row)
    return subjects
