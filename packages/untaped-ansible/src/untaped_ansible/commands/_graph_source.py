"""Which source a graph command reads, and in which direction.

:func:`graph_source_of` names the selected sources (or the inline selectors,
or ``ansible.default_source``) as one :class:`GraphSource`;
:func:`effective_direction` narrows the direction to what that source can
answer, and :func:`refresh_hint` says how to fill a source that holds nothing.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass

from untaped.sdk import System, UsageError, command_line, not_found
from untaped_ansible.application.graph import GraphDirection
from untaped_ansible.application.ports import DependencyIndex
from untaped_ansible.commands._ansible import inline_source_key, saved_source_key, source_definition
from untaped_ansible.commands._source_data import SourceData
from untaped_ansible.errors import AnsibleError
from untaped_ansible.infrastructure import MultiSourceDependencyIndex
from untaped_ansible.refresh import SelectedSource
from untaped_ansible.settings import SOURCES, SourceDefinition


@dataclass(frozen=True)
class GraphSource:
    selections: tuple[SelectedSource, ...]
    key: str | None
    label: str | None
    saved: bool

    @property
    def header(self) -> str | None:
        """How the tree header names it: ``source prod``, ``sources a, b``, ``inline source K``."""
        if self.saved and len(self.selections) == 1:
            return f"source {self.label}"
        return self.label


def graph_source_of(data: SourceData, *, default_source: str | None) -> GraphSource:
    """The explicit selection, else ``ansible.default_source``, else no source."""
    names = list(dict.fromkeys(data.source))
    if names and data.inline:
        raise UsageError(
            "--source cannot be combined with --org, --team, --repo, --path, "
            "--ref-kind, --ref-pattern, or --ref-scan-default"
        )
    from_default = False
    if not names and not data.inline and default_source is not None:
        names, from_default = [default_source], True
    if names:
        selections: list[SelectedSource] = []
        for name in names:
            source = SOURCES.get(name)
            if source is None:
                known = sorted(entry.name for entry in SOURCES.all())
                message = not_found("source", name, known=known)
                if from_default:
                    # The user's own setting names a missing source: fix the config.
                    message = f"{message} (set by ansible.default_source)"
                raise AnsibleError(
                    message, category="config" if from_default else "not_found", system=System.LOCAL
                )
            selections.append(
                SelectedSource(
                    definition=source,
                    key=saved_source_key(name),
                    label=name,
                    subject=f"source {name}",
                )
            )
        return GraphSource(
            selections=tuple(selections),
            key=_graph_source_key(selections),
            label=_graph_source_label(selections),
            saved=True,
        )
    if data.inline:
        source = source_definition(
            name="<inline>",
            orgs=data.org,
            teams=data.team,
            repos=data.repo,
            paths=data.path,
            ref_kinds=data.ref_kind,
            ref_patterns=data.ref_pattern,
            ref_scan_default=data.ref_scan_default,
        )
        key = inline_source_key(source)
        label = _inline_label(source)
        return GraphSource(
            selections=(SelectedSource(definition=source, key=key, label=label, subject=label),),
            key=key,
            label=label,
            saved=False,
        )
    return GraphSource(selections=(), key=None, label=None, saved=False)


def _inline_label(source: SourceDefinition) -> str:
    """``inline source --org acme --repo acme/x``: the selector flags, in help order."""
    flags: list[str] = []
    for flag, values in (
        ("--org", source.orgs),
        ("--team", source.teams),
        ("--repo", source.repos),
        ("--path", source.dependency_paths),
        ("--ref-kind", source.ref_kinds),
        ("--ref-pattern", source.ref_patterns),
        ("--ref-scan-default", [source.ref_scan_default] if source.ref_scan_default else []),
    ):
        for value in values:
            flags += [flag, value]
    return f"inline source {shlex.join(flags)}"


def _graph_source_key(selections: list[SelectedSource]) -> str:
    if len(selections) == 1:
        return selections[0].key
    names = ",".join(selection.key.removeprefix("source:") for selection in selections)
    return f"sources:{names}"


def _graph_source_label(selections: list[SelectedSource]) -> str:
    if len(selections) == 1:
        return selections[0].label
    return f"sources {', '.join(selection.label for selection in selections)}"


def dependency_index_for_graph_source(
    index: DependencyIndex, source_state: GraphSource
) -> DependencyIndex:
    if len(source_state.selections) <= 1:
        return index
    return MultiSourceDependencyIndex(
        index, tuple(selection.key for selection in source_state.selections)
    )


def effective_direction(
    source_state: GraphSource, *, direction: GraphDirection
) -> tuple[GraphDirection, list[str]]:
    """``direction``, downstream only (with a warning) when no source can answer upstream."""
    if source_state.selections or direction == "deps":
        return direction, []
    message = (
        "upstream requires --source NAME, inline selectors like --org, --team, or --repo, "
        "or ansible.default_source"
    )
    if direction == "impact":
        raise UsageError(message)
    return "deps", [
        "only showing downstream; upstream omitted because no source is configured. "
        "Pass --source NAME or inline selectors, or set ansible.default_source."
    ]


def refresh_hint(source_state: GraphSource) -> str | None:
    """Compose the exact fix command surfaced in missing-ref warnings."""
    if not source_state.selections:
        return None
    if source_state.saved:
        from untaped_ansible.commands.source import Refresh  # noqa: PLC0415 - import cycle

        commands = " and ".join(
            f"`{command_line(Refresh.spelled(shlex.quote(selection.label)))}`"
            for selection in source_state.selections
        )
        return f"Run {commands} to update it."
    return "Re-run this command with `--refresh` to update it."
