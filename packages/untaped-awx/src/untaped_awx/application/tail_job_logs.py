"""Use case: tail a job's stdout with optional regex / tail-N filters.

Two phases:

- **Drain** the existing log via :meth:`JobMonitor.fetch_stdout`. The
  resulting list is bounded to the last ``--tail N`` lines if requested
  (retained memory is then ``O(tail)``, not ``O(N)``), then filtered by
  ``--grep PATTERN`` (Python regex, optional ``--ignore-case``).
- **Follow** (only if ``follow=True``) keeps polling
  :meth:`JobMonitor.stream_stdout` from where the drain left off, again
  filtered by the same pattern, until the job hits a terminal state.

Splitting the two phases makes ``--tail N`` semantically clean: we only
trim the historical block, not the live tail.
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Iterable, Iterator
from re import Pattern

from untaped_awx.application.ports import JobMonitor
from untaped_awx.domain import Job


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
        existing = self._monitor.fetch_stdout(job, start_line=0)
        cursor = len(existing)
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
        if not follow:
            return
        # Live follow: pick up where the drain left off and let the
        # monitor's own polling drive terminal detection.
        for line in self._monitor.stream_stdout(job, start_line=cursor):
            if _matches(line, pattern):
                yield line


def _compile_pattern(grep: str | None, *, ignore_case: bool) -> Pattern[str] | None:
    if grep is None:
        return None
    flags = re.IGNORECASE if ignore_case else 0
    return re.compile(grep, flags)


def _matches(line: str, pattern: Pattern[str] | None) -> bool:
    return pattern is None or pattern.search(line) is not None
