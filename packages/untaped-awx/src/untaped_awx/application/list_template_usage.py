"""Use case: list the workflows that *contain* a template.

The reverse of :class:`ListWorkflowNodes` — answers "which workflows run
this job template / workflow template?". ``max_depth`` controls how far
the ancestry walk goes: ``0`` (default) returns only direct parents;
``None`` recurses without limit; ``N>0`` caps at depth N.

Identifier resolution uses template names by default and numeric ids
only when the caller requests explicit id mode. The walk is
breadth-first with per-entry ancestor tracking, so true cycles emit a
stderr warning while workflows reachable along several paths (diamond
pattern) are emitted once, at their shallowest depth. ``node_count``
on each row counts that workflow's *direct* references to the child
whose parents were being queried at that step — depth-0 rows count
references to the target itself, depth-1 rows count references to the
depth-0 workflow that led there, and so on.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable
from typing import Any

from untaped_awx.application.get_resource import resolve_identity
from untaped_awx.application.ports import (
    ResourceClient,
    WorkflowNodeRepository,
)
from untaped_awx.domain import ResourceSpec, WorkflowUsage


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
    def __init__(
        self,
        nodes: WorkflowNodeRepository,
        resources: ResourceClient,
        *,
        warn: Callable[[str], None] = lambda _msg: None,
    ) -> None:
        self._nodes = nodes
        self._resources = resources
        self._warn = warn

    def __call__(
        self,
        spec: ResourceSpec,
        *,
        identifier: str,
        scope: dict[str, str] | None = None,
        by_id: bool = False,
        max_depth: int | None = 0,
        filters: dict[str, str] | None = None,
    ) -> list[WorkflowUsage]:
        target_id = resolve_identity(self._resources, spec, identifier, scope=scope, by_id=by_id)
        out: list[WorkflowUsage] = []
        listed: set[int] = {target_id}
        queue: deque[tuple[int, int, frozenset[int]]] = deque([(target_id, 0, frozenset())])
        while queue:
            child_id, depth, ancestors = queue.popleft()
            new_ancestors = ancestors | {child_id}
            references = _aggregate_references(
                self._nodes.list_references(unified_job_template=child_id, params=filters)
            )
            for wf_id, (name, count) in references.items():
                if wf_id in new_ancestors:
                    self._warn(
                        f"cycle: workflow {wf_id} already visited; skipping",
                    )
                    continue
                if wf_id in listed:
                    continue
                listed.add(wf_id)
                out.append(WorkflowUsage(id=wf_id, name=name, depth=depth, node_count=count))
                if max_depth is not None and depth + 1 > max_depth:
                    continue
                queue.append((wf_id, depth + 1, new_ancestors))
        return out
