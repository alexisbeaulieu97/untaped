"""A workflow job's nodes as they ran: what each ran, how it ended, which one failed it.

AWX runs a workflow job as one node per node of its template
(``workflow_jobs/<id>/workflow_nodes/``). Each keeps the template node's
``identifier``, names the execution it started (``job``, ``null`` for a node
that never ran) with that execution's type and status in
``summary_fields.job``, and lists the nodes it leads to. A workflow fails when
a node fails with no ``failure`` or ``always`` path out of it (AWX's "No error
handling path"): :func:`blamed_node` finds that node. The host summaries of
its node jobs sum into the workflow's own (:func:`summed_host_records`). A
workflow template's own nodes (:class:`TemplateNode`) say what it will run,
and which nodes wait for an approval (:func:`approval_labels`). Nested
workflows are followed :data:`MAX_NESTING` levels deep. Pure: no I/O.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict

from untaped.capabilities.awx.domain.job import Job

APPROVAL = "workflow_approval"
"""The execution type of an approval node."""
WORKFLOW_JOB = "workflow_job"
"""The execution type of a workflow (a nested workflow node's too)."""
NEVER_RAN = "never_ran"
"""The status of a node that started nothing."""
MAX_NESTING = 5
"""Levels of workflows nested in a workflow that are followed (for approvals, attribution)."""

_FAILED = frozenset({"failed", "error", "canceled"})
_HOST_COUNTERS = ("ok", "changed", "failures", "dark", "skipped", "rescued", "ignored")
"""The counters of a ``job_host_summaries`` record."""


class NodeResult(BaseModel):
    """One node of a workflow case's row: what it ran and how that ended."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str | None
    """The node's id: AWX's node ``identifier``."""
    template: str | None
    """What the node ran: the template's name (the approval's, for an approval node)."""
    job_id: int | None
    """The execution the node started (``None``: it never ran)."""
    status: str
    """That execution's status, or ``never_ran``."""


@dataclass(frozen=True, slots=True)
class RunNode:
    """One node of a workflow job, from its ``workflow_nodes`` record."""

    record_id: int
    identifier: str | None
    template: str | None
    job_id: int | None
    kind: str | None
    """The execution's type: ``job``, ``workflow_job``, ``workflow_approval``, …"""
    status: str
    error_path: bool
    """Whether a ``failure`` or ``always`` path leads out of the node."""

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> RunNode:
        summary = record.get("summary_fields") or {}
        job = summary.get("job") or {}
        template = summary.get("unified_job_template") or {}
        job_id = record.get("job")
        started = isinstance(job_id, int)
        return cls(
            record_id=int(record["id"]),
            identifier=record.get("identifier"),
            template=template.get("name"),
            job_id=job_id if started else None,
            kind=job.get("type") or template.get("unified_job_type"),
            status=str(job.get("status") or "unknown") if started else NEVER_RAN,
            error_path=bool(record.get("failure_nodes") or record.get("always_nodes")),
        )

    @property
    def label(self) -> str:
        """How messages name the node: its id, else AWX's record id."""
        return self.identifier or f"#{self.record_id}"

    @property
    def failed(self) -> bool:
        return self.status in _FAILED

    @property
    def execution(self) -> Job | None:
        """The execution the node started, as the node last saw it (``None``: never ran)."""
        if self.job_id is None or self.kind is None:
            return None
        return Job(id=self.job_id, kind=self.kind, name=self.template, status=self.status)

    def result(self) -> NodeResult:
        return NodeResult(
            id=self.identifier, template=self.template, job_id=self.job_id, status=self.status
        )


def blamed_node(nodes: Sequence[RunNode]) -> RunNode | None:
    """The node that failed the workflow: the first to fail with no path out of its failure.

    ``None`` when every failure had a path out of it (the workflow failed for
    another reason, such as a node whose template was deleted), or none failed.
    """
    failed = sorted((node for node in nodes if node.failed), key=lambda node: node.job_id or 0)
    return next((node for node in failed if not node.error_path), None)


@dataclass(frozen=True, slots=True)
class TemplateNode:
    """One node of a workflow template, from its ``workflow_nodes`` record."""

    record_id: int
    identifier: str | None
    template: str | None
    """What the node runs: the template's name (the approval's, for an approval node)."""
    template_id: int | None
    kind: str | None
    """The type of execution it starts: ``job``, ``workflow_job``, ``workflow_approval``, …"""

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> TemplateNode:
        summary = (record.get("summary_fields") or {}).get("unified_job_template") or {}
        template_id = record.get("unified_job_template")
        return cls(
            record_id=int(record["id"]),
            identifier=record.get("identifier"),
            template=summary.get("name"),
            template_id=template_id if isinstance(template_id, int) else None,
            kind=summary.get("unified_job_type"),
        )

    @property
    def label(self) -> str:
        """How messages name the node: its id, else AWX's record id."""
        return self.identifier or f"#{self.record_id}"


def approval_labels(nodes: Iterable[TemplateNode]) -> list[str]:
    """The ids of the nodes that wait for an approval."""
    return [node.label for node in nodes if node.kind == APPROVAL]


def summed_host_records(per_job: Iterable[Iterable[Mapping[str, Any]]]) -> list[dict[str, Any]]:
    """One host summary record per host, its counters summed over every job's.

    Failed hosts come first, then by name, as a job's own summaries are read.
    """
    totals: dict[str, dict[str, int]] = {}
    for records in per_job:
        for record in records:
            host = str(record.get("host_name") or record.get("host"))
            total = totals.setdefault(host, dict.fromkeys(_HOST_COUNTERS, 0))
            for counter in _HOST_COUNTERS:
                total[counter] += record.get(counter) or 0
    rows = [
        {"host_name": host, **counts, "failed": bool(counts["failures"] or counts["dark"])}
        for host, counts in totals.items()
    ]
    return sorted(rows, key=lambda row: (not row["failed"], row["host_name"]))


__all__ = [
    "APPROVAL",
    "MAX_NESTING",
    "NEVER_RAN",
    "WORKFLOW_JOB",
    "NodeResult",
    "RunNode",
    "TemplateNode",
    "approval_labels",
    "blamed_node",
    "summed_host_records",
]
