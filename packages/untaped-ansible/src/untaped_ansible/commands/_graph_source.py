"""Which source a graph command reads, and in which direction.

:func:`graph_source_of` names the selected sources (or
``ansible.default_source``) as one :class:`GraphSource`;
:func:`effective_direction` narrows the direction to what that source can
answer, and :func:`refresh_hint` says how to fill a source that holds nothing.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass

from untaped.sdk import System, UsageError, command_line, not_found
from untaped_ansible.application.graph import GraphDirection
from untaped_ansible.application.ports import DependencyIndex
from untaped_ansible.commands._ansible import saved_source_key
from untaped_ansible.commands._source_data import SourceData
from untaped_ansible.errors import AnsibleError
from untaped_ansible.infrastructure import MultiSourceDependencyIndex
from untaped_ansible.refresh import SelectedSource
from untaped_ansible.sources import SOURCES


@dataclass(frozen=True)
class GraphSource:
    selections: tuple[SelectedSource, ...]
    key: str | None
    label: str | None

    @property
    def header(self) -> str | None:
        """How the tree header names it: ``source prod``, ``sources a, b``."""
        if len(self.selections) == 1:
            return f"source {self.label}"
        return self.label


def graph_source_of(data: SourceData, *, default_source: str | None) -> GraphSource:
    """The explicit selection, else ``ansible.default_source``, else no source."""
    names = list(dict.fromkeys(data.source))
    from_default = False
    if not names and default_source is not None:
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
        )
    return GraphSource(selections=(), key=None, label=None)


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
    message = "upstream requires --source NAME or ansible.default_source"
    if direction == "impact":
        raise UsageError(message)
    return "deps", [
        "only showing downstream; upstream omitted because no source is configured. "
        "Pass --source NAME, or set ansible.default_source."
    ]


def refresh_hint(source_state: GraphSource) -> str | None:
    """Compose the exact fix command surfaced in missing-ref warnings."""
    if not source_state.selections:
        return None
    from untaped_ansible.commands.source import Refresh  # noqa: PLC0415 - import cycle

    commands = " and ".join(
        f"`{command_line(Refresh.spelled(shlex.quote(selection.label)))}`"
        for selection in source_state.selections
    )
    return f"Run {commands} to update it."
