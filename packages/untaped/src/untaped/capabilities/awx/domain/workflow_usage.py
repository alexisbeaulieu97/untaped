"""DTO for one workflow that *contains* a queried template.

The reverse of :class:`WorkflowNode`: where a node row describes "this
workflow runs that template", a usage row answers "that template is run
by these workflows". Each row aggregates every node of one containing
``WorkflowJobTemplate`` that references the queried template, so there
is no single raw AWX record to preserve — hence no ``summary_fields``
passthrough (and no dotted-path projections). ``depth`` records how far
up the ancestry the container sits when callers walk parent workflows
recursively (``0`` for direct parents); ``node_count`` counts the
container's *direct* references to the child whose parents were being
queried at that step.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class WorkflowUsage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    id: int
    name: str | None = None
    depth: int = 0
    node_count: int
