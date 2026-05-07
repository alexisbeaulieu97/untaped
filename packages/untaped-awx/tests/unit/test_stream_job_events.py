"""Unit tests for :class:`StreamJobEvents`."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

from untaped_awx.application.stream_job_events import StreamJobEvents
from untaped_awx.domain import Job, JobEvent


class _FakeMonitor:
    def __init__(self, *, events: list[JobEvent] | None = None) -> None:
        self.events = list(events or [])
        self.stream_calls: list[tuple[int, dict[str, str] | None, str]] = []

    def fetch(self, job: Job) -> Job:
        return job

    def fetch_stdout(self, *args: Any, **kwargs: Any) -> list[str]:
        raise NotImplementedError

    def stream_stdout(self, *args: Any, **kwargs: Any) -> Iterable[str]:
        raise NotImplementedError

    def stream_events(
        self,
        job: Job,
        *,
        from_counter: int = 0,
        params: dict[str, str] | None = None,
    ) -> Iterator[JobEvent]:
        self.stream_calls.append((from_counter, params, job.status))
        for ev in self.events:
            if ev.counter > from_counter:
                yield ev


def _running() -> Job:
    return Job(id=1, kind="job", status="running")


def _ev(counter: int, **fields: Any) -> JobEvent:
    return JobEvent(counter=counter, **fields)


def test_drain_marks_job_terminal_for_non_follow() -> None:
    """Without --follow the use case forces a 'successful' shadow status
    so the monitor's polling loop drains and returns immediately."""
    monitor = _FakeMonitor(events=[_ev(1), _ev(2)])
    list(StreamJobEvents(monitor)(_running(), follow=False))
    # The job we passed to the monitor was a copy with status flipped.
    assert monitor.stream_calls[0][2] == "successful"


def test_follow_passes_real_status_through() -> None:
    monitor = _FakeMonitor()
    list(StreamJobEvents(monitor)(_running(), follow=True))
    assert monitor.stream_calls[0][2] == "running"


def test_from_counter_skips_already_seen_events() -> None:
    monitor = _FakeMonitor(events=[_ev(1), _ev(2), _ev(3)])
    out = list(StreamJobEvents(monitor)(_running(), from_counter=1))
    assert [e.counter for e in out] == [2, 3]


def test_filters_forwarded_to_monitor_unchanged() -> None:
    monitor = _FakeMonitor()
    filters = {"event": "runner_on_failed", "host": "web-01"}
    list(StreamJobEvents(monitor)(_running(), filters=filters))
    assert monitor.stream_calls[0][1] == filters
