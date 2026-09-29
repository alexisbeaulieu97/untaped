"""Domain model for an AWX async execution (job, workflow_job, project_update, …).

All four kinds normalise to the same surface for the CLI: a numeric id, a
status string, a kind discriminator, and a few timing fields. Streaming
events are exposed as :class:`JobEvent` lines (:attr:`JobEvent.lines` is
their stdout without ANSI colours), and :class:`HostSummary` is one host's
PLAY RECAP counters. :func:`poll_until_terminal` is the one polling
loop every waiter and streamer drives (the fetch and sleep are injected, so
this module still performs no I/O itself).
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from itertools import islice
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

TERMINAL_STATUSES = frozenset({"successful", "failed", "error", "canceled"})


@dataclass(frozen=True)
class JobRoutes:
    """Supported status, event and stdout surface for an execution kind."""

    collection: str
    events: str | None
    stdout: bool = True
    relaunch: bool = False
    """Whether ``<collection>/<id>/relaunch/`` exists."""
    template_field: str | None = None
    """The field naming the template (or project/source) that ran it."""
    ui_type: str = "playbook"
    """The web UI's name for the kind in ``jobs/<ui_type>/<id>/output``."""


JOB_ROUTES: dict[str, JobRoutes] = {
    "job": JobRoutes("jobs", "job_events", relaunch=True, template_field="job_template"),
    "workflow_job": JobRoutes(
        "workflow_jobs",
        None,
        stdout=False,
        relaunch=True,
        template_field="workflow_job_template",
        ui_type="workflow",
    ),
    "project_update": JobRoutes(
        "project_updates", "events", template_field="project", ui_type="project"
    ),
    "inventory_update": JobRoutes(
        "inventory_updates", "events", template_field="inventory_source", ui_type="inventory"
    ),
    "ad_hoc_command": JobRoutes("ad_hoc_commands", "events", relaunch=True, ui_type="command"),
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
    scm_branch: str | None = None
    """The branch, tag or commit a job was launched on (empty: its project's)."""
    scm_revision: str | None = None
    """The commit a job's project checkout resolved to."""
    event_processing_finished: bool | None = Field(default=None, exclude=True)
    """``False`` while AWX is still saving a finished job's events."""
    job_explanation: str | None = Field(default=None, exclude=True)
    """AWX's note on why the job ended, e.g. ``Previous Task Failed: {…}``."""
    result_traceback: str | None = Field(default=None, exclude=True)
    """The controller-side traceback of a job that ended in ``error``."""

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES


def still_running_detail(job: Job, timeout: float | None) -> str:
    """Describe an execution a timed-out wait left unfinished."""
    return f"still {job.status} after --timeout {timeout or 0:g}s"


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
    res: dict[str, Any] | None = Field(default=None, exclude=True)
    """The module result (``event_data.res``); left out of rendered rows."""

    @property
    def lines(self) -> list[str]:
        """The event's stdout lines as the text log shows them (ANSI colours removed)."""
        return strip_ansi(self.stdout).splitlines()

    @model_validator(mode="before")
    @classmethod
    def _lift_result(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("event_data"), dict):
            res = data["event_data"].get("res")
            if isinstance(res, dict):
                return {**data, "res": res}
        return data


_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def strip_ansi(text: str) -> str:
    """``text`` without ANSI escape sequences (AWX stores event stdout coloured)."""
    return _ANSI.sub("", text)


class HostSummary(BaseModel):
    """One host's PLAY RECAP counters, from ``jobs/<id>/job_host_summaries/``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ok: int = 0
    changed: int = 0
    failed: int = 0
    unreachable: int = 0
    skipped: int = 0
    rescued: int = 0
    ignored: int = 0

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> HostSummary:
        """AWX's ``failures`` is ``failed`` and its ``dark`` is ``unreachable``, as in the recap."""
        return cls(
            ok=record.get("ok") or 0,
            changed=record.get("changed") or 0,
            failed=record.get("failures") or 0,
            unreachable=record.get("dark") or 0,
            skipped=record.get("skipped") or 0,
            rescued=record.get("rescued") or 0,
            ignored=record.get("ignored") or 0,
        )


HOST_SUMMARY_LIMIT = 500
"""Hosts a result keeps; a larger job's summary is cut and marked truncated."""


def host_summaries(records: Iterable[Mapping[str, Any]]) -> tuple[dict[str, HostSummary], bool]:
    """The first :data:`HOST_SUMMARY_LIMIT` hosts' summaries, by name, and whether more exist.

    ``records`` may be a lazy paginated read (read failed hosts first, so the
    cut never drops them): at most one record past the limit is consumed.
    """
    kept = list(islice(records, HOST_SUMMARY_LIMIT + 1))
    hosts = {
        str(record.get("host_name") or record.get("host")): HostSummary.from_record(record)
        for record in kept[:HOST_SUMMARY_LIMIT]
    }
    return dict(sorted(hosts.items())), len(kept) > HOST_SUMMARY_LIMIT
