"""Domain model for an AWX async execution (job, workflow_job, project_update, …).

All four kinds normalise to the same surface for the CLI: a numeric id, a
status string, a kind discriminator, and a few timing fields. Streaming
events are exposed as :class:`JobEvent` lines.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

TERMINAL_STATUSES = frozenset({"successful", "failed", "error", "canceled"})


class Job(BaseModel):
    """A single async execution record."""

    model_config = ConfigDict(extra="ignore")

    id: int
    kind: str
    """One of ``job``, ``workflow_job``, ``project_update``, ``inventory_update``."""

    name: str | None = None
    status: str
    started: str | None = None
    finished: str | None = None
    failed: bool = False

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES


class JobEvent(BaseModel):
    """A line of stdout / structured event from a running job."""

    model_config = ConfigDict(extra="ignore")

    counter: int
    stdout: str
