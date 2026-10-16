"""Ports of the job use cases: watching, following and cancelling an execution.

Concrete adapters live in ``infrastructure/jobs/`` and are wired together at
the CLI composition root.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from untaped_awx.domain import Job, JobEvent


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


__all__ = ["Canceller", "JobMonitor"]
