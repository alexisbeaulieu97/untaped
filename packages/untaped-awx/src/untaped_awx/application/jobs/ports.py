"""Ports of the job use cases: reading, watching, following and cancelling an execution.

The controller's adapter serves :class:`JobRecords` and :class:`JobOutput`
(:mod:`untaped_awx.infrastructure.controller`); a :class:`JobMonitor`
(:mod:`untaped_awx.application.jobs.monitor`) polls over both, its
:class:`Executions`.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from typing import Any, Protocol

from untaped_awx.domain import Job, JobEvent


class JobRecords(Protocol):
    """Execution records: listed, read, cancelled and relaunched; a run's hosts and nodes.

    ``kind`` is a :class:`Job` kind (``job``, ``workflow_job``, ...).
    """

    def jobs(
        self,
        *,
        kind: str,
        params: dict[str, str] | None = None,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """``kind`` executions ``params`` select, newest first unless they order them."""
        ...

    def job(self, *, kind: str, job_id: int) -> dict[str, Any]:
        """One execution record."""
        ...

    def job_host_summaries(
        self, job: Job, params: Mapping[str, str] | None = None
    ) -> Iterator[dict[str, Any]]:
        """``job``'s PLAY RECAP per host, read lazily, failed and unreachable hosts first."""
        ...

    def workflow_job_nodes(self, job: Job) -> Iterator[dict[str, Any]]:
        """The nodes of a workflow job, as they ran."""
        ...

    def decide_approval(self, approval_id: int, *, approve: bool) -> None:
        """Approve or deny a pending workflow approval."""
        ...

    def cancel_job(self, *, kind: str, job_id: int) -> None:
        """Ask the controller to cancel an execution; it stops it later."""
        ...

    def relaunch_job(self, *, kind: str, job_id: int, hosts: str | None = None) -> dict[str, Any]:
        """Relaunch an execution (on ``hosts`` alone, when given); the new execution record."""
        ...


class JobOutput(Protocol):
    """An execution's log: its whole stdout at once, or its events."""

    def job_stdout(self, job: Job) -> str:
        """``job``'s whole log as text, never truncated."""
        ...

    def job_events(self, job: Job, params: Mapping[str, str]) -> Iterator[dict[str, Any]]:
        """Every page of ``job``'s events ``params`` select."""
        ...

    def job_events_page(self, job: Job, params: Mapping[str, str]) -> list[dict[str, Any]]:
        """One page of ``job``'s events, as ``params`` order and size it."""
        ...


class Executions(JobRecords, JobOutput, Protocol):
    """Execution records and their logs: what a :class:`JobMonitor` polls."""


class Canceller(Protocol):
    """Ask AWX to cancel a running execution (raises when AWX refuses)."""

    def __call__(self, *, kind: str, job_id: int) -> None: ...


class JobMonitor(Protocol):
    """Polls a Job, its stdout, and its structured events until terminal.

    AWX has no SSE/websocket surface in v2 — "live" means polling. This
    Protocol abstracts the polling cadence so use cases can be unit-tested
    against a synchronous stub (a list-of-events stand-in is enough).
    """

    def fetch(self, job: Job) -> Job:
        """Re-fetch ``job``'s record so callers can see status transitions."""
        ...

    def stream_status(self, job: Job) -> Iterable[Job]:
        """Yield the initial status and changes until terminal via the detail endpoint."""
        ...

    def fetch_stdout(self, job: Job) -> list[str]:
        """One-shot: return every stdout line (the full log, never truncated).

        No polling: ``jobs logs`` without ``--follow`` drains the existing
        log with it, and ``awx test`` reads it for log expectations.
        """
        ...

    def settled(self, job: Job) -> Job:
        """``job`` re-read (briefly) until AWX has saved a finished job's events."""
        ...

    def tail_stdout(self, job: Job, lines: int) -> tuple[list[str], int]:
        """The log's last ``lines`` lines, read from its newest events only.

        Returns them with the newest event's counter (``0`` when there is
        none), where a :meth:`stream_stdout` that follows them starts.
        """
        ...

    def stream_stdout(self, job: Job, *, from_counter: int = 0) -> Iterable[str]:
        """Yield the log lines of the events after ``from_counter`` until terminal.

        Each poll reads only the events it has not read yet (ANSI colours
        removed), one string per line (no trailing newline). The events of the
        terminal state are yielded before the iterator returns.
        """
        ...

    def stream_events(
        self,
        job: Job,
        *,
        from_counter: int = 0,
        params: dict[str, str] | None = None,
        follow: bool = True,
    ) -> Iterable[JobEvent]:
        """Yield :class:`JobEvent` rows in counter order.

        ``from_counter`` is exclusive (matches AWX's ``counter__gt`` query
        param). Extra ``params`` are forwarded server-side so callers can
        push native filters like ``event=runner_on_failed`` without
        client-side post-filtering. ``follow=False`` drains the existing
        events once and returns; ``follow=True`` polls until the job is
        terminal.
        """
        ...


__all__ = ["Canceller", "Executions", "JobMonitor", "JobOutput", "JobRecords"]
