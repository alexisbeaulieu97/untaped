"""AbandonJobs: what becomes of an execution a wait stops watching.

``launch``/``sync --wait --cancel`` and ``awx test run`` share this policy.
An execution abandoned on a timeout, a polling error or Ctrl-C is cancelled
when a :class:`Canceller` is given, and otherwise keeps running; either way
the caller gets the phrase its row or message reports.
"""

from __future__ import annotations

from collections.abc import Iterable

from untaped.capabilities.awx.application.ports import Canceller
from untaped.capabilities.awx.domain import Job


class AbandonJobs:
    """Leave or cancel executions a wait stops watching, remembering the cancelled."""

    def __init__(self, canceller: Canceller | None = None) -> None:
        self._cancel = canceller
        """``None`` leaves abandoned executions running."""
        self.cancelled: set[tuple[str, int]] = set()
        """``(kind, id)`` of executions whose cancel AWX accepted."""

    @property
    def cancels(self) -> bool:
        """Whether abandoned executions are cancelled rather than left running."""
        return self._cancel is not None

    def __call__(self, job: Job) -> str:
        """Abandon ``job``; say what became of it."""
        if self._cancel is None:
            return "it keeps running"
        try:
            self._cancel(kind=job.kind, job_id=job.id)
        except Exception as exc:
            return f"cancel failed: {exc}"
        self.cancelled.add((job.kind, job.id))
        return "cancel requested"

    def unfinished(self, jobs: Iterable[Job]) -> None:
        """Abandon every execution not known to have ended nor already cancelled."""
        for job in jobs:
            if not job.is_terminal and (job.kind, job.id) not in self.cancelled:
                self(job)


__all__ = ["AbandonJobs"]
