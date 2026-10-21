"""DTO for one workflow that *contains* a queried template, and the tree of them.

The reverse of :class:`WorkflowNode`: where a node row describes "this
workflow runs that template", a usage row answers "that template is run
by these workflows". Each row aggregates every node of one containing
``WorkflowJobTemplate`` that references the queried template or another
row, so there is no single raw AWX record to preserve — hence no
``summary_fields`` passthrough (and no dotted-path projections).
``contains`` holds the row's links to what it runs, so the rows of one
target are its whole ancestry graph; :func:`usage_tree` draws it.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from untaped.sdk import Record, TreeGraph, TreeGraphEdge, TreeGraphNode, plural
from untaped_awx.domain.workflow_node import WorkflowNodeType


class ContainedTemplate(BaseModel):
    """One template a workflow runs: the queried template, or another row's workflow."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    id: int = Field(description="The template's AWX id.")
    type: WorkflowNodeType = Field(
        description="`job_template`, or `workflow_job_template` for another row's workflow."
    )
    node_count: int = Field(description="How many of the workflow's nodes run it.")


class WorkflowUsage(Record, kind="awx.template_usage"):
    """One workflow that runs a queried template, directly or through other workflows (``usage``).

    ``depth`` is the shortest distance to the queried template (``0``: it
    runs it directly) and ``node_count`` the number of its nodes running the
    template at that distance: both describe ``contains[0]``. ``contains``
    lists what it runs among the queried template and the other rows (only
    the links the walk saw), ``target`` is the queried template's id, and
    ``stopped`` is ``depth`` when the depth limit stopped the walk before
    this workflow's own containers were looked up.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")
    table_columns: ClassVar[tuple[str, ...]] = ("id", "name", "depth", "node_count")

    id: int
    name: str | None = None
    depth: int = 0
    node_count: int
    contains: list[ContainedTemplate] = Field(default_factory=list)
    target: int
    stopped: Literal["depth"] | None = None


class UsageTarget(BaseModel):
    """The template whose usage was asked: the tree's root."""

    model_config = ConfigDict(frozen=True)

    id: int
    name: str | None = None
    type: Literal["job_template", "workflow_job_template"]


class UsageAnswer(BaseModel):
    """One target's usage rows, in the order the walk found them."""

    model_config = ConfigDict(frozen=True)

    target: UsageTarget
    rows: tuple[WorkflowUsage, ...]


_KIND_WORDS = {"job_template": "job template", "workflow_job_template": "workflow template"}


def usage_tree(target: UsageTarget, rows: Sequence[WorkflowUsage], *, top_level: bool) -> TreeGraph:
    """``target``'s rows drawn as a tree: each workflow under every template it runs.

    Everything below the first line comes from the rows. A workflow is
    labelled by its name, with its id only when another workflow of the tree
    shares that name. A top-level workflow (not stopped, run by no other
    row) is drawn in the strong tone and listed in the summary, unless
    ``top_level`` is off (a filtered lookup can hide a workflow's
    containers). The target's own row (a cycle through it) adds edges into
    the root rather than a node.
    """
    others = [row for row in rows if row.id != target.id]
    names = _names(target, others)
    labels = _labels(target, names)

    def order(id_: int) -> tuple[str, int]:
        return names[id_].casefold(), id_

    contained = {link.id for row in rows for link in row.contains}
    tops = (
        {row.id for row in others if row.stopped is None and row.id not in contained}
        if top_level
        else set()
    )
    nodes = tuple(
        TreeGraphNode(
            key=str(row.id),
            label=labels[row.id],
            stopped=row.stopped is not None,
            tone="strong" if row.id in tops else "plain",
        )
        for row in others
    )
    edges = sorted(
        (
            TreeGraphEdge(
                parent=str(link.id),
                child=str(row.id),
                suffix=f"({link.node_count} nodes)" if link.node_count > 1 else "",
            )
            for row in rows
            for link in row.contains
        ),
        key=lambda edge: order(int(edge.child)),
    )
    kind = _KIND_WORDS[target.type]
    return TreeGraph(
        root=TreeGraphNode(key=str(target.id), label=labels[target.id]),
        notes=(kind, "used by" if rows else "in no workflow"),
        nodes=nodes,
        edges=tuple(edges),
        summary=_summary(others, [labels[id_] for id_ in sorted(tops, key=order)]),
    )


def _names(target: UsageTarget, rows: Sequence[WorkflowUsage]) -> dict[int, str]:
    """Each template's name by id, ``workflow #id`` for a workflow AWX sent none for."""
    names = {target.id: target.name or f"{_KIND_WORDS[target.type]} #{target.id}"}
    return names | {row.id: row.name or f"workflow #{row.id}" for row in rows}


def _labels(target: UsageTarget, names: dict[int, str]) -> dict[int, str]:
    """Each name, plus ``#id`` when two workflows of the tree share it.

    A job template target is never drawn as a row, so it doesn't count: the
    first line already tells it apart from a workflow of the same name.
    """
    workflows = {
        id_: name
        for id_, name in names.items()
        if id_ != target.id or target.type == "workflow_job_template"
    }
    counts = Counter(workflows.values())
    return {
        id_: f"{name} #{id_}" if id_ in workflows and counts[name] > 1 else name
        for id_, name in names.items()
    }


def _summary(rows: Sequence[WorkflowUsage], tops: list[str]) -> str | None:
    """``4 workflows · 1 stopped · top-level: a, b`` (each part when it applies)."""
    if not rows:
        return None
    parts = [plural(len(rows), "workflow")]
    if stopped := sum(1 for row in rows if row.stopped is not None):
        parts.append(f"{stopped} stopped")
    if tops:
        parts.append(f"top-level: {', '.join(tops)}")
    return " · ".join(parts)
