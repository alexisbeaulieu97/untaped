"""The one AWX scheduler: bounded, dependency-ordered, stop-on-failure."""

from __future__ import annotations

import threading
from contextvars import ContextVar

import pytest

from untaped.capabilities.awx.application.scheduling import (
    MAX_PARALLEL,
    Schedule,
    ScheduleInterrupted,
)


def test_serial_run_follows_lowest_ready_index_after_dependencies() -> None:
    started: list[int] = []

    def work(index: int) -> str:
        started.append(index)
        return f"done:{index}"

    results = Schedule(parallel=1).run(
        4, work, skipped=lambda index: f"skipped:{index}", dependencies=[(2,), (), (), (0,)]
    )
    assert started == [1, 2, 0, 3]
    assert results == {index: f"done:{index}" for index in range(4)}


def test_stop_skips_items_that_have_not_started() -> None:
    schedule = Schedule(parallel=1)

    def work(index: int) -> str:
        if index == 1:
            schedule.stop()
        return f"done:{index}"

    results = schedule.run(4, work, skipped=lambda index: f"skipped:{index}")
    assert [results[index] for index in range(4)] == ["done:0", "done:1", "skipped:2", "skipped:3"]
    assert schedule.stopped


def test_blocked_replaces_running_an_item_with_failed_dependencies() -> None:
    ran: list[int] = []

    def work(index: int) -> str:
        ran.append(index)
        return "failed" if index == 0 else "ok"

    results = Schedule(parallel=2).run(
        3,
        work,
        skipped=lambda index: "skipped",
        dependencies=[(), (0,), ()],
        blocked=lambda index, deps: "blocked" if "failed" in deps else None,
    )
    assert results == {0: "failed", 1: "blocked", 2: "ok"}
    assert sorted(ran) == [0, 2]


def test_dependency_cycles_never_start() -> None:
    results = Schedule(parallel=2).run(
        3, lambda index: index, skipped=lambda index: -1, dependencies=[(1,), (0,), ()]
    )
    assert results == {2: 2}


def test_parallelism_is_bounded() -> None:
    barrier = threading.Barrier(MAX_PARALLEL, timeout=2)
    lock = threading.Lock()
    active = maximum = 0

    def work(index: int) -> int:
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        barrier.wait()
        with lock:
            active -= 1
        return index

    Schedule(parallel=100).run(MAX_PARALLEL * 2, work, skipped=lambda index: -1)
    assert maximum == MAX_PARALLEL


def test_workers_see_the_callers_context() -> None:
    marker: ContextVar[str] = ContextVar("marker", default="unset")
    marker.set("caller")
    results = Schedule(parallel=4).run(4, lambda _index: marker.get(), skipped=lambda _: "")
    assert set(results.values()) == {"caller"}


def test_interrupt_reports_finished_items_and_releases_dependency_waits() -> None:
    def work(index: int) -> int:
        if index == 1:
            raise KeyboardInterrupt
        return index

    with pytest.raises(ScheduleInterrupted) as caught:
        Schedule(parallel=1).run(3, work, skipped=lambda index: -1, dependencies=[(), (0,), (1,)])
    assert caught.value.results == {0: 0}


def test_parallel_must_be_positive() -> None:
    with pytest.raises(ValueError, match="parallel"):
        Schedule(parallel=0)
