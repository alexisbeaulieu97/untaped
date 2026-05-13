"""DTO for a single workflow job template node.

A frozen view of one row in ``/api/v2/workflow_job_templates/<id>/
workflow_nodes/``, flattened so the CLI can render a table without
reaching into raw ``summary_fields`` dicts. ``depth`` records how far
the node is from the root workflow when callers expand sub-workflows
recursively (``0`` for the root's own nodes).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

WorkflowNodeType = Literal[
    "job_template",
    "workflow_job_template",
    "project",
    "inventory_source",
]
"""Template-type discriminator the rest of the CLI exposes."""


# AWX's ``summary_fields.unified_job_template.unified_job_type`` is the
# *job* (execution) discriminator, not the *template* type — a node
# referencing a WorkflowJobTemplate reports ``"workflow_job"``, not
# ``"workflow_job_template"``.
_JOB_TYPE_TO_TEMPLATE_TYPE: dict[str, WorkflowNodeType] = {
    "job": "job_template",
    "workflow_job": "workflow_job_template",
    "project_update": "project",
    "inventory_update": "inventory_source",
}


def normalise_unified_job_type(raw: str | None) -> WorkflowNodeType | None:
    """Map AWX's ``unified_job_type`` to the template-type discriminator.

    Returns ``None`` for unknown values so the recursion guard never
    descends into kinds we don't recognise.
    """
    if raw is None:
        return None
    return _JOB_TYPE_TO_TEMPLATE_TYPE.get(raw)


class WorkflowNode(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    id: int
    identifier: str | None = None
    workflow_job_template: int
    unified_job_template: int | None = None
    name: str | None = None
    type: WorkflowNodeType | None = None
    depth: int = 0
