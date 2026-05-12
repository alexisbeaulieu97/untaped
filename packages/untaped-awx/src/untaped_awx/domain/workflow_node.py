"""DTO for a single workflow job template node.

A frozen view of one row in ``/api/v2/workflow_job_templates/<id>/
workflow_nodes/``, flattened so the CLI can render a table without
reaching into raw ``summary_fields`` dicts. ``depth`` records how far
the node is from the root workflow when callers expand sub-workflows
recursively (``0`` for the root's own nodes).

The "what runs here" reference (the unified-job-template FK) is split
into three flat columns — ``unified_job_template`` (the id),
``name`` and ``type`` (flattened from ``summary_fields``) — so
``--columns name,type`` works the same way as on every other AWX
``list`` command without forcing users to type the longer FK path.
Fields are optional where AWX can omit them: a node whose referenced
template has been deleted carries ``unified_job_template: null``, and
``identifier`` was added after the node concept itself so older nodes
may have ``identifier: null``.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class WorkflowNode(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")

    id: int
    identifier: str | None = None
    workflow_job_template: int
    unified_job_template: int | None = None
    name: str | None = None
    type: str | None = None
    depth: int = 0
