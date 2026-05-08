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
        yield from self._monitor.stream_events(
            job, from_counter=from_counter, params=filters, follow=follow
        )
