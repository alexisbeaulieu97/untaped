"""Domain model for an AWX async execution (job, workflow_job, project_update, …).

All four kinds normalise to the same surface for the CLI: a numeric id, a
status string, a kind discriminator, and a few timing fields. Streaming
events are exposed as :class:`JobEvent` lines. :func:`poll_until_terminal`
is the one polling loop every waiter and streamer drives (the fetch and
sleep are injected, so this module still performs no I/O itself).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

TERMINAL_STATUSES = frozenset({"successful", "failed", "error", "canceled"})


@dataclass(frozen=True)
class JobRoutes:
    """Supported status, event and stdout surface for an execution kind."""

    collection: str
    events: str | None
    stdout: bool = True


JOB_ROUTES: dict[str, JobRoutes] = {
    "job": JobRoutes("jobs", "job_events"),
    "workflow_job": JobRoutes("workflow_jobs", None, stdout=False),
    "project_update": JobRoutes("project_updates", "events"),
    "inventory_update": JobRoutes("inventory_updates", "events"),
    "ad_hoc_command": JobRoutes("ad_hoc_commands", "events"),
}
KIND_TO_API_PATH: dict[str, str] = {kind: routes.collection for kind, routes in JOB_ROUTES.items()}
"""Derived status collection map shared by readers and waiters."""


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


def poll_until_terminal(
    job: Job,
    fetch: Callable[[Job], Job],
    *,
    sleep: Callable[[float], None],
    interval: float,
    timeout: float | None = None,
) -> Iterator[Job]:
    """Yield ``job``, then each re-fetched state, until one is terminal.

    Sleeps ``interval`` before every fetch. With ``timeout`` the loop also
    stops (after yielding the latest state) once that many seconds passed.
    """
    deadline = time.monotonic() + timeout if timeout is not None else None
    current = job
    yield current
    while not current.is_terminal:
        if deadline is not None and time.monotonic() >= deadline:
            return
        sleep(interval)
        current = fetch(current)
        yield current


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
