"""Use case: list the workflows that *contain* a template.

The reverse of :class:`ListWorkflowNodes` — answers "which workflows run
this job template / workflow template?". ``max_depth`` controls how far
the ancestry walk goes: ``0`` (default) returns only direct parents;
``None`` recurses without limit; ``N>0`` caps at depth N.

Identifier resolution uses template names by default and numeric ids
only when the caller requests explicit id mode; either way the target's
record is read, since the tree names it. The walk is breadth-first and
looks each workflow's containers up once: a workflow reached along
several paths (a diamond) is one row, at its shallowest depth, whose
``contains`` lists every link the walk saw. A cycle is a link like any
other, and a workflow template in its own ancestry is a row of its own
usage (its link needs a row to live on); the tree marks cycles, so the
command warns about them from there.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from typing import Any, Literal

from untaped_awx.application.resources.get_resource import parse_resource_id
from untaped_awx.application.resources.ports import ResourceClient, WorkflowNodeRepository
from untaped_awx.domain import ResourceSpec
from untaped_awx.domain.workflow_usage import (
    ContainedTemplate,
    UsageAnswer,
    UsageTarget,
    WorkflowUsage,
)
from untaped_awx.errors import ResourceNotFoundError

_TEMPLATE_TYPES: dict[str, Literal["job_template", "workflow_job_template"]] = {
    "JobTemplate": "job_template",
    "WorkflowJobTemplate": "workflow_job_template",
}


def _aggregate_references(rows: Iterable[dict[str, Any]]) -> dict[int, tuple[str | None, int]]:
    """Aggregate workflow-node rows into per-workflow ``(name, reference-count)``.

    AWX repeats identical ``summary_fields`` on every node row of a workflow,
    so the name is taken from the first row seen; server order is preserved.
    """
    usage: dict[int, tuple[str | None, int]] = {}
    for raw in rows:
        wf_id = int(raw["workflow_job_template"])
        if wf_id not in usage:
            summary = raw.get("summary_fields") or {}
            name = (summary.get("workflow_job_template") or {}).get("name")
            usage[wf_id] = (name, 0)
        name, count = usage[wf_id]
        usage[wf_id] = (name, count + 1)
    return usage


class ListTemplateUsage:
    def __init__(self, nodes: WorkflowNodeRepository, resources: ResourceClient) -> None:
        self._nodes = nodes
        self._resources = resources

    def __call__(
        self,
        spec: ResourceSpec,
        *,
        identifier: str,
        scope: dict[str, str] | None = None,
        by_id: bool = False,
        max_depth: int | None = 0,
        filters: dict[str, str] | None = None,
    ) -> UsageAnswer:
        target = self._target(spec, identifier, scope=scope, by_id=by_id)
        rows: dict[int, dict[str, Any]] = {}
        queue: deque[tuple[int, int]] = deque([(target.id, 0)])
        while queue:
            child_id, depth = queue.popleft()
            child_type = target.type if child_id == target.id else "workflow_job_template"
            references = _aggregate_references(
                self._nodes.list_references(unified_job_template=child_id, params=filters)
            )
            for wf_id, (name, count) in references.items():
                link = ContainedTemplate(id=child_id, type=child_type, node_count=count)
                if wf_id in rows:
                    rows[wf_id]["contains"].append(link)
                    continue
                row: dict[str, Any] = {
                    "id": wf_id,
                    "name": name,
                    "depth": depth,
                    "node_count": count,
                    "contains": [link],
                    "target": target.id,
                }
                rows[wf_id] = row
                if wf_id == target.id:
                    continue  # its containers were the first lookup
                if max_depth is not None and depth + 1 > max_depth:
                    row["stopped"] = "depth"
                    continue
                queue.append((wf_id, depth + 1))
        return UsageAnswer(target=target, rows=tuple(WorkflowUsage(**row) for row in rows.values()))

    def _target(
        self, spec: ResourceSpec, identifier: str, *, scope: dict[str, str] | None, by_id: bool
    ) -> UsageTarget:
        """The queried template's record: by id, or by name within ``scope``."""
        if by_id:
            id_ = parse_resource_id(identifier)
            record = self._resources.find(spec, params={"id": str(id_)})
            if record is None:
                raise ResourceNotFoundError(spec.kind, {"id": id_})
        else:
            record = self._resources.find_by_identity(spec, name=identifier, scope=scope)
            if record is None:
                raise ResourceNotFoundError(spec.kind, {"name": identifier, **(scope or {})})
        return UsageTarget(id=record.id, name=record.name, type=_TEMPLATE_TYPES[spec.kind])
