"""Unit tests for :class:`TailJobLogs`.

Stubs ``JobMonitor`` so we can exercise the drain and follow logic
without a polling loop. The stub's log has one line per event, so a line's
position is its event counter.
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
        self.stream_stdout_calls: list[int] = []
        self.downloads = 0

    def fetch(self, job: Job) -> Job:
        return job

    def fetch_stdout(self, job: Job) -> list[str]:
        self.downloads += 1
        return list(self.existing)

    def tail_stdout(self, job: Job, lines: int) -> tuple[list[str], int]:
        return (self.existing[-lines:] if lines > 0 else []), len(self.existing)

    def stream_stdout(self, job: Job, *, from_counter: int = 0) -> Iterator[str]:
        self.stream_stdout_calls.append(from_counter)
        return iter([*self.existing[from_counter:], *self.live])

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
        # --follow reads the whole log through events, then the live lines
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
    if options.get("follow"):
        # Following never downloads the whole log; --tail starts after the tail's events.
        start = len(existing) if "tail" in options else 0
        assert (monitor.stream_stdout_calls, monitor.downloads) == ([start], 0)
    else:
        assert (monitor.stream_stdout_calls, monitor.downloads) == ([], 1)


@pytest.mark.parametrize(("saved", "downloads"), [(True, 1), (False, 0), (None, 0)])
def test_following_a_finished_job_downloads_its_saved_log_once(
    saved: bool | None, downloads: int
) -> None:
    monitor = _FakeMonitor(existing=["a", "b", "c"])
    job = Job(id=1, kind="job", status="failed", event_processing_finished=saved)
    assert list(TailJobLogs(monitor)(job, follow=True, tail=2)) == ["b", "c"]
    assert monitor.downloads == downloads
