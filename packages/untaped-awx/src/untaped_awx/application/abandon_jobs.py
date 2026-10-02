"""AbandonJobs: what becomes of an execution a wait stops watching.

``launch``/``sync --wait --cancel`` and ``awx test run`` share this policy.
An execution abandoned on a timeout, a polling error or Ctrl-C is cancelled
when a :class:`Canceller` is given, and otherwise keeps running; either way
the caller gets the phrase its row or message reports. A refused cancel is
re-read (with a refresher): an execution that ended meanwhile says so.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from untaped_awx.application.ports import Canceller
from untaped_awx.domain import Job


class AbandonJobs:
    """Leave or cancel executions a wait stops watching, remembering the cancelled."""

    def __init__(
        self,
        canceller: Canceller | None = None,
        *,
        refresher: Callable[[Job], Job] | None = None,
    ) -> None:
        self._cancel = canceller
        """``None`` leaves abandoned executions running."""
        self._refresh = refresher
        """Re-reads an execution whose cancel AWX refused."""
        self.cancelled: set[tuple[str, int]] = set()
        """``(kind, id)`` of executions whose cancel AWX accepted."""
        self._ended: dict[tuple[str, int], Job] = {}

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
            ended = self._ended_meanwhile(job)
            if ended is not None:
                return f"it ended ({ended.status}) before the cancel"
            return f"cancel failed: {exc}"
        self.cancelled.add((job.kind, job.id))
        return "cancel requested"

    def latest(self, job: Job) -> Job:
        """``job`` as last known: re-read when it ended before its cancel."""
        return self._ended.get((job.kind, job.id), job)

    def unfinished(self, jobs: Iterable[Job]) -> None:
        """Abandon every execution not known to have ended nor already cancelled."""
        for job in jobs:
            if not self.latest(job).is_terminal and (job.kind, job.id) not in self.cancelled:
                self(job)

    def _ended_meanwhile(self, job: Job) -> Job | None:
        """``job`` re-read after a refused cancel, when it has ended by now."""
        if self._refresh is None:
            return None
        try:
            latest = self._refresh(job)
        except Exception:
            return None
        if not latest.is_terminal:
            return None
        self._ended[(job.kind, job.id)] = latest
        return latest


__all__ = ["AbandonJobs"]
