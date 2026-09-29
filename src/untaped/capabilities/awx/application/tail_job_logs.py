"""Use case: tail a job's stdout with optional regex / tail-N filters.

Without ``follow`` the existing log is **drained** once via
:meth:`JobMonitor.fetch_stdout`, bounded to the last ``--tail N`` lines if
requested (retained memory is then ``O(tail)``, not ``O(N)``), then filtered
by ``--grep PATTERN`` (Python regex, optional ``--ignore-case``).

With ``follow`` the log is read through the job's events instead, so no poll
downloads the whole log again: ``--tail N`` reads only the newest events
(:meth:`JobMonitor.tail_stdout`), then :meth:`JobMonitor.stream_stdout`
polls for the events after them until the job hits a terminal state (without
``--tail``, from the first event). ``--tail`` trims only the historical
block, never the live tail; ``--grep`` filters both.
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Iterable, Iterator
from re import Pattern

from untaped.capabilities.awx.application.ports import JobMonitor
from untaped.capabilities.awx.domain import Job


class TailJobLogs:
    def __init__(self, monitor: JobMonitor) -> None:
        self._monitor = monitor

    def __call__(
        self,
        job: Job,
        *,
        follow: bool = False,
        grep: str | None = None,
        ignore_case: bool = False,
        tail: int | None = None,
    ) -> Iterable[str]:
        pattern = _compile_pattern(grep, ignore_case=ignore_case)
        return self._iter(job, follow=follow, pattern=pattern, tail=tail)

    def _iter(
        self,
        job: Job,
        *,
        follow: bool,
        pattern: Pattern[str] | None,
        tail: int | None,
    ) -> Iterator[str]:
        if follow:
            yield from self._follow(job, pattern=pattern, tail=tail)
            return
        existing = self._monitor.fetch_stdout(job)
        historical: Iterable[str]
        if tail is None:
            historical = existing
        elif tail <= 0:
            # ``--tail 0`` means "skip historical entirely" — distinct
            # from negative indexing where ``existing[-0:]`` would return
            # the whole list.
            historical = []
            existing = []
        else:
            # Bounded retention: ``deque(maxlen=N)`` keeps only the last
            # N references. After construction we drop ``existing`` so
            # the full log list can be GC'd during the filter loop —
            # important for jobs with very large stdout where ``tail``
            # is small (e.g. ``--tail 50`` on a 100k-line log).
            historical = deque(existing, maxlen=tail)
            existing = []
        for line in historical:
            if _matches(line, pattern):
                yield line

    def _follow(self, job: Job, *, pattern: Pattern[str] | None, tail: int | None) -> Iterator[str]:
        after = 0
        if tail is not None:
            historical, after = self._monitor.tail_stdout(job, tail)
            yield from (line for line in historical if _matches(line, pattern))
        # The monitor's own polling drives terminal detection.
        for line in self._monitor.stream_stdout(job, from_counter=after):
            if _matches(line, pattern):
                yield line


def _compile_pattern(grep: str | None, *, ignore_case: bool) -> Pattern[str] | None:
    if grep is None:
        return None
    flags = re.IGNORECASE if ignore_case else 0
    return re.compile(grep, flags)


def _matches(line: str, pattern: Pattern[str] | None) -> bool:
    return pattern is None or pattern.search(line) is not None
