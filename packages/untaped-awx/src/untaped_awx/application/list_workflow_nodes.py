"""Use case: list the nodes of a ``WorkflowJobTemplate``.

Answers the question "which jobs run inside this workflow?". ``max_depth``
controls recursion: ``0`` (default) returns only the root's nodes;
``None`` recurses without limit; ``N>0`` caps at depth N. Edges
(``success_nodes`` / ``failure_nodes`` / ``always_nodes``) are
deliberately out of scope here — this surface is about *contents*, not
the DAG structure.

Identifier resolution accepts either a numeric workflow id (fast path,
no name lookup) or a name plus optional FK-name scope. Traversal is
breadth-first with per-entry ancestor tracking, so true cycles emit a
stderr warning while shared sub-workflows (diamond pattern) are
skipped silently — both avoid re-fetching the same workflow's contents.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from typing import Any

from untaped_awx.application.ports import (
    ResourceClient,
    WorkflowNodeRepository,
)
from untaped_awx.domain import ResourceSpec, WorkflowNode, normalise_unified_job_type
from untaped_awx.errors import ResourceNotFound


class ListWorkflowNodes:
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
        max_depth: int | None = 0,
        filters: dict[str, str] | None = None,
    ) -> list[WorkflowNode]:
        root_id = self._resolve(spec, identifier, scope=scope)
        out: list[WorkflowNode] = []
        listed: set[int] = {root_id}
        queue: deque[tuple[int, int, frozenset[int]]] = deque([(root_id, 0, frozenset())])
        while queue:
            workflow_id, depth, ancestors = queue.popleft()
            new_ancestors = ancestors | {workflow_id}
            for raw in self._nodes.list_nodes(workflow_id=workflow_id, params=filters):
                node = _build_node(raw, workflow_id=workflow_id, depth=depth)
                out.append(node)
                child_id = node.unified_job_template
                if (
                    node.type != "workflow_job_template"
                    or child_id is None
                    or (max_depth is not None and depth + 1 > max_depth)
                ):
                    continue
                if child_id in new_ancestors:
                    self._warn(
                        f"cycle: workflow {child_id} already visited; skipping",
                    )
                    continue
                if child_id in listed:
                    continue
                listed.add(child_id)
                queue.append((child_id, depth + 1, new_ancestors))
        return out

    def _resolve(
        self,
        spec: ResourceSpec,
        identifier: str,
        *,
        scope: dict[str, str] | None,
    ) -> int:
        if identifier.isdecimal():
            return int(identifier)
        record = self._resources.find_by_identity(spec, name=identifier, scope=scope)
        if record is None:
            raise ResourceNotFound(spec.kind, {"name": identifier, **(scope or {})})
        return record.id


def _build_node(raw: dict[str, Any], *, workflow_id: int, depth: int) -> WorkflowNode:
    summary = raw.get("summary_fields") or {}
    ujt_summary = summary.get("unified_job_template") or {}
    ujt_id = raw.get("unified_job_template")
    return WorkflowNode(
        id=int(raw["id"]),
        identifier=raw.get("identifier"),
        workflow_job_template=workflow_id,
        unified_job_template=int(ujt_id) if isinstance(ujt_id, int) else None,
        name=ujt_summary.get("name"),
        type=normalise_unified_job_type(ujt_summary.get("unified_job_type")),
        depth=depth,
        summary_fields=summary,
    )
