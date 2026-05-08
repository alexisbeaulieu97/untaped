"""Unit tests for :class:`TailJobLogs`.

Stubs ``JobMonitor`` so we can exercise the drain-then-follow logic
without a polling loop.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

from untaped_awx.application.tail_job_logs import TailJobLogs
from untaped_awx.domain import Job, JobEvent


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


def test_drain_only_no_follow() -> None:
    monitor = _FakeMonitor(existing=["a", "b", "c"])
    out = list(TailJobLogs(monitor)(_terminal()))
    assert out == ["a", "b", "c"]
    assert monitor.stream_stdout_calls == []  # follow mode never engaged


def test_tail_trims_historical_block() -> None:
    monitor = _FakeMonitor(existing=["a", "b", "c", "d", "e"])
    out = list(TailJobLogs(monitor)(_terminal(), tail=2))
    assert out == ["d", "e"]


def test_tail_zero_emits_no_history_but_still_follows() -> None:
    """``--tail 0 --follow`` skips history; only live lines reach the user."""
    monitor = _FakeMonitor(existing=["historical"], live=["live-1", "live-2"])
    out = list(TailJobLogs(monitor)(_running(), tail=0, follow=True))
    assert out == ["live-1", "live-2"]


def test_grep_filters_lines_client_side() -> None:
    monitor = _FakeMonitor(existing=["INFO ok", "ERROR boom", "INFO done"])
    out = list(TailJobLogs(monitor)(_terminal(), grep="ERROR"))
    assert out == ["ERROR boom"]


def test_grep_ignore_case() -> None:
    monitor = _FakeMonitor(existing=["INFO ok", "error: boom"])
    out = list(TailJobLogs(monitor)(_terminal(), grep="ERROR", ignore_case=True))
    assert out == ["error: boom"]


def test_follow_emits_live_after_drain() -> None:
    monitor = _FakeMonitor(existing=["hist-1", "hist-2"], live=["live-1", "live-2"])
    out = list(TailJobLogs(monitor)(_running(), follow=True))
    assert out == ["hist-1", "hist-2", "live-1", "live-2"]
    # Live tail picks up where the drain left off.
    assert monitor.stream_stdout_calls == [2]


def test_follow_with_grep_filters_both_phases() -> None:
    monitor = _FakeMonitor(existing=["INFO ok", "ERROR hist"], live=["INFO running", "ERROR live"])
    out = list(TailJobLogs(monitor)(_running(), follow=True, grep="ERROR"))
    assert out == ["ERROR hist", "ERROR live"]


def test_follow_with_tail_only_trims_historical() -> None:
    monitor = _FakeMonitor(existing=["a", "b", "c"], live=["live"])
    out = list(TailJobLogs(monitor)(_running(), follow=True, tail=1))
    assert out == ["c", "live"]


def test_tail_drops_full_buffer_reference_during_iteration() -> None:
    """``--tail N`` keeps only ``N`` historical lines reachable.

    Non-functional but observable contract: once iteration starts, the
    full ``fetch_stdout`` list must no longer be strongly reachable
    from the use case. A regression to ``existing[-tail:]`` (which
    keeps the original list alive through the filter loop) would fail
    this test. We probe via :mod:`weakref` rather than asserting on
    ``deque`` membership directly — the *observable* property is "the
    full list is GC-eligible," which is what the user actually cares
    about, and which holds independently of the bounding primitive.
    """
    import gc
    import weakref

    class _Trackable(list[str]):
        """``list`` subclass — built-in ``list`` has no ``__weakref__`` slot."""

    big_log = [f"line {i}" for i in range(100)]
    captured: list[weakref.ref[_Trackable]] = []

    class _SpyMonitor:
        def fetch_stdout(self, job: Job, *, start_line: int = 0) -> list[str]:
            buf = _Trackable(big_log)  # fresh list per call so the weakref is meaningful
            captured.append(weakref.ref(buf))
            return buf

        def fetch(self, job: Job) -> Job:
            return job

        def stream_stdout(self, job: Job, *, start_line: int = 0) -> Iterator[str]:
            return iter([])

        def stream_events(self, *args: Any, **kwargs: Any) -> Iterable[JobEvent]:
            raise NotImplementedError

    iterator = iter(TailJobLogs(_SpyMonitor())(_terminal(), tail=5))
    first = next(iterator)  # runs through ``existing = []`` and into the loop

    gc.collect()
    assert captured[0]() is None, (
        "fetch_stdout's full list is still strongly reachable after the "
        "deque was constructed; check that the bounded branch still drops "
        "``existing`` before the first ``yield``."
    )

    rest = list(iterator)
    assert [first, *rest] == big_log[-5:]
