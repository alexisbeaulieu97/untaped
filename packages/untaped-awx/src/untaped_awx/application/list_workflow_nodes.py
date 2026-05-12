"""Use case: list the nodes of a ``WorkflowJobTemplate``.

Answers the question "which jobs run inside this workflow?". The
optional ``recursive`` mode flattens sub-workflows so a single command
shows everything that actually executes, depth-tagged for grouping.
Edges (``success_nodes`` / ``failure_nodes`` / ``always_nodes``) are
deliberately out of scope here — this surface is about *contents*, not
the DAG structure.

Identifier resolution accepts either a numeric workflow id (fast path,
no name lookup) or a name plus optional FK-name scope (resolves via
:meth:`ResourceClient.find_by_identity`). Recursion is cycle-guarded by
a ``visited`` set on workflow id; a workflow that re-enters itself
emits a stderr warning via the injected ``warn`` hook and is skipped.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from typing import Any

from untaped_awx.application.ports import (
    ResourceClient,
    WorkflowNodeRepository,
)
from untaped_awx.domain import ResourceSpec, WorkflowNode
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
        recursive: bool = False,
        max_depth: int | None = None,
    ) -> list[WorkflowNode]:
        root_id = self._resolve(spec, identifier, scope=scope)
        out: list[WorkflowNode] = []
        visited: set[int] = set()
        queue: deque[tuple[int, int]] = deque([(root_id, 0)])
        while queue:
            workflow_id, depth = queue.popleft()
            if workflow_id in visited:
                # The root workflow is added to ``visited`` before the
                # first listing, so this branch fires only on a true
                # recursive re-entry (workflow A → workflow B → workflow A).
                self._warn(
                    f"cycle: workflow {workflow_id} already visited; skipping",
                )
                continue
            visited.add(workflow_id)
            for raw in self._nodes.list_nodes(workflow_id=workflow_id):
                node = _build_node(raw, workflow_id=workflow_id, depth=depth)
                out.append(node)
                if (
                    recursive
                    and node.type == "workflow_job_template"
                    and node.unified_job_template is not None
                    and (max_depth is None or depth + 1 <= max_depth)
                ):
                    queue.append((node.unified_job_template, depth + 1))
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
        type=_normalise_type(ujt_summary.get("unified_job_type") or ujt_summary.get("type")),
        depth=depth,
    )


# AWX's ``summary_fields.unified_job_template.unified_job_type`` is the
# *job* (execution) discriminator, not the *template* type — a workflow
# node referencing a WorkflowJobTemplate reports ``"workflow_job"``, not
# ``"workflow_job_template"``. Normalise to the template-type
# discriminator the rest of the codebase (and the user) expects, so
# ``--columns type`` matches ``unified-templates``' output and the
# recursion guard in :class:`ListWorkflowNodes` can test against
# ``"workflow_job_template"`` directly.
_JOB_TYPE_TO_TEMPLATE_TYPE: dict[str, str] = {
    "job": "job_template",
    "workflow_job": "workflow_job_template",
    "project_update": "project",
    "inventory_update": "inventory_source",
}


def _normalise_type(raw: str | None) -> str | None:
    if raw is None:
        return None
    return _JOB_TYPE_TO_TEMPLATE_TYPE.get(raw, raw)
