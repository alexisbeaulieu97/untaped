"""Domain model for an AWX async execution (job, workflow_job, project_update, …).

All four kinds normalise to the same surface for the CLI: a numeric id, a
status string, a kind discriminator, and a few timing fields. Streaming
events are exposed as :class:`JobEvent` lines.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

TERMINAL_STATUSES = frozenset({"successful", "failed", "error", "canceled"})

KIND_TO_API_PATH: dict[str, str] = {
    "job": "jobs",
    "workflow_job": "workflow_jobs",
    "project_update": "project_updates",
    "inventory_update": "inventory_updates",
    "ad_hoc_command": "ad_hoc_commands",
}
"""Map an execution-record :attr:`Job.kind` to its AWX collection path.

Lives in domain because the kind→endpoint relationship is intrinsic to a
Job (not a transport detail). Both the application use case
(:class:`untaped_awx.application.WatchJob`) and the infrastructure
adapter (:class:`untaped_awx.infrastructure.PollingJobMonitor`) read it,
so domain is the only place where neither would be importing the other's
internals."""


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
    """A structured per-task event from a running job.

    AWX emits one event per playbook lifecycle transition (``playbook_on_play_start``,
    ``playbook_on_task_start``, ``runner_on_ok`` / ``runner_on_failed`` / …)
    plus the per-host result rows. ``counter`` is monotonically increasing
    inside a job so callers tail by ``counter__gt=N`` to fetch only new
    events.

    ``extra="ignore"`` keeps us tolerant to AWX's verbose row shape (it
    returns 30+ fields per event, most of them noise) without having to
    enumerate them all.
    """

    model_config = ConfigDict(extra="ignore")

    counter: int
    event: str = ""
    """AWX event-name discriminator (e.g. ``playbook_on_task_start``)."""

    task: str | None = None
    host: int | None = None
    """FK id of the target host. Use ``host_name`` for the rendered name —
    AWX denormalises it because the underlying ``Host`` record can be
    deleted while events that reference it linger."""

    host_name: str | None = None
    role: str | None = None
    play: str | None = None
    changed: bool = False
    failed: bool = False
    created: str | None = None
    stdout: str = ""
