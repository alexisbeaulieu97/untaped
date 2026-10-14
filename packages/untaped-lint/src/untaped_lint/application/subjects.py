"""Where a run's subjects come from: the plugin repository it runs in, or ``--stdin``.

Inside a plugin repository the nearest ``pyproject.toml`` (up to the project
root) names its plugins under ``[project.entry-points."untaped.plugins"]``;
each installed one is an ``untaped.plugin`` subject, and one that is not
installed is a finding, never a subject. ``--stdin`` reads pipe envelopes,
each one subject of its kind.
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from untaped.sdk import PipeEnvelope, Plugin, UsageError, kind_of, project_root
from untaped_lint.records import Finding, Subject

PLUGIN_KIND = kind_of(Plugin) or "untaped.plugin"
_GROUP = "untaped.plugins"


@dataclass
class Found:
    """The subjects of a run and the findings that kept a declared one from being one."""

    subjects: list[Subject] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)


def plugin_subject(plugin: Plugin) -> Subject:
    return Subject(kind=PLUGIN_KIND, record=plugin.model_dump(mode="json"))


def declared_plugins(start: Path) -> list[str]:
    """The plugin names the nearest ``pyproject.toml`` from ``start`` up declares."""
    start = start.resolve()
    top = project_root(start)
    for folder in (start, *start.parents):
        project = folder / "pyproject.toml"
        if project.is_file():
            return _entry_points(project)
        if folder == top:
            break
    return []


def _entry_points(project: Path) -> list[str]:
    try:
        data = tomllib.loads(project.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise UsageError(f"cannot read {project}: {exc}") from None
    group = data.get("project", {}).get("entry-points", {}).get(_GROUP, {})
    return sorted(group) if isinstance(group, dict) else []


def repository_subjects(names: Iterable[str], installed: Sequence[Plugin]) -> Found:
    """A subject per declared plugin that is installed; a finding for one that isn't."""
    by_name = {plugin.name: plugin for plugin in installed}
    found = Found()
    for name in names:
        plugin = by_name.get(name)
        if plugin is None:
            found.findings.append(not_installed(name))
        else:
            found.subjects.append(plugin_subject(plugin))
    return found


def not_installed(name: str) -> Finding:
    return Finding(
        rule="lint.plugin-not-installed",
        severity="error",
        target=name,
        message=f"{name} is declared in pyproject.toml but not installed",
        hint="install the project in this environment, as in `uv pip install -e .`",
    )


def stdin_subjects(envelopes: Iterable[PipeEnvelope]) -> list[Subject]:
    """Each pipe envelope as a subject of its kind; a kind-less one is a usage error."""
    subjects: list[Subject] = []
    for envelope in envelopes:
        if not envelope.kind:
            raise UsageError("a record on stdin has no kind; pipe untaped's --format pipe output")
        subjects.append(Subject(kind=envelope.kind, record=dict(envelope.record)))
    return subjects
