"""Unit tests for :class:`TailJobLogs`.

Stubs ``JobMonitor`` so we can exercise the drain-then-follow logic
without a polling loop.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

import pytest

from untaped.capabilities.awx.application.tail_job_logs import TailJobLogs
from untaped.capabilities.awx.domain import Job, JobEvent


class _FakeMonitor:
    def __init__(
        self,
        *,
        existing: list[str] | None = None,
        live: list[str] | None = None,
    ) -> None:
        self.existing = list(existing or [])
        self.live = list(live or [])
        self.fetch_stdout_calls: list[int] = []
        self.stream_stdout_calls: list[int] = []

    def fetch(self, job: Job) -> Job:
        return job

    def fetch_stdout(self, job: Job, *, start_line: int = 0) -> list[str]:
        self.fetch_stdout_calls.append(start_line)
        return self.existing[start_line:]

    def stream_stdout(self, job: Job, *, start_line: int = 0) -> Iterator[str]:
        self.stream_stdout_calls.append(start_line)
        return iter(self.live)

    def stream_events(self, *args: Any, **kwargs: Any) -> Iterable[JobEvent]:
        raise NotImplementedError


def _running() -> Job:
    return Job(id=1, kind="job", status="running")


def _terminal() -> Job:
    return Job(id=1, kind="job", status="successful")


@pytest.mark.parametrize(
    ("existing", "live", "options", "expected"),
    [
        (["a", "b", "c"], [], {}, ["a", "b", "c"]),
        (["a", "b", "c", "d", "e"], [], {"tail": 2}, ["d", "e"]),
        (["INFO ok", "ERROR boom", "INFO done"], [], {"grep": "ERROR"}, ["ERROR boom"]),
        (["INFO ok", "error: boom"], [], {"grep": "ERROR", "ignore_case": True}, ["error: boom"]),
        # --follow drains history, then tails live lines from where it left off
        (["h1", "h2"], ["l1", "l2"], {"follow": True}, ["h1", "h2", "l1", "l2"]),
        (["INFO ok", "ERROR h"], ["INFO r", "ERROR l"], {"follow": True, "grep": "ERROR"},
         ["ERROR h", "ERROR l"]),
        (["a", "b", "c"], ["live"], {"follow": True, "tail": 1}, ["c", "live"]),
        (["historical"], ["l1", "l2"], {"follow": True, "tail": 0}, ["l1", "l2"]),
    ],
)  # fmt: skip
def test_tail_job_logs(
    existing: list[str], live: list[str], options: dict[str, Any], expected: list[str]
) -> None:
    monitor = _FakeMonitor(existing=existing, live=live)
    job = _running() if options.get("follow") else _terminal()
    assert list(TailJobLogs(monitor)(job, **options)) == expected
    assert monitor.stream_stdout_calls == ([len(existing)] if options.get("follow") else [])
