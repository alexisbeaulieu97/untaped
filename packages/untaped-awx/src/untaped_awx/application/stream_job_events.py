"""Use case: yield :class:`JobEvent`s from a :class:`JobMonitor` stream.

Server-side filtering is forwarded to AWX as query params (the CLI's
``--filter KEY=VALUE`` lands here unchanged). Application code does no
client-side post-filtering — when the user asks for typed-field
filters, AWX is the cheapest and most expressive place to do it.

Same drain-then-follow shape as :class:`TailJobLogs`: we always emit
the existing event log first, then optionally keep polling for new
events until the job reaches a terminal status.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from untaped_awx.application.ports import JobMonitor
from untaped_awx.domain import Job, JobEvent


class StreamJobEvents:
    def __init__(self, monitor: JobMonitor) -> None:
        self._monitor = monitor

    def __call__(
        self,
        job: Job,
        *,
        from_counter: int = 0,
        filters: dict[str, str] | None = None,
        follow: bool = False,
    ) -> Iterable[JobEvent]:
        return self._iter(job, from_counter=from_counter, filters=filters, follow=follow)

    def _iter(
        self,
        job: Job,
        *,
        from_counter: int,
        filters: dict[str, str] | None,
        follow: bool,
    ) -> Iterator[JobEvent]:
        # Defensive copy: marking the job terminal forces the monitor's
        # stream_events loop to drain once and return without polling
        # again. For follow=True we let the real status pass through so
        # the monitor keeps polling until AWX flips it terminal itself.
        drain_job = job if follow else job.model_copy(update={"status": "successful"})
        yield from self._monitor.stream_events(drain_job, from_counter=from_counter, params=filters)
