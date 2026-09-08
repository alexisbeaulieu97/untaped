"""Independent action scheduling preserves bounded in-flight and skipped outcomes."""

from threading import Barrier, Event, Lock
from typing import Any

import pytest

from untaped.capabilities.awx.application.selected_actions import run_selected_actions
from untaped.capabilities.awx.application.selection import SelectedResource


def targets(count: int) -> list[SelectedResource]:
    return [
        SelectedResource("Project", index, str(index), {}, {"id": index})
        for index in range(1, count + 1)
    ]


@pytest.mark.parametrize("continue_on_error", [False, True])
def test_serial_stops_after_failure_unless_continue_requested(continue_on_error: bool) -> None:
    writes: list[int] = []

    def worker(target: SelectedResource) -> int:
        writes.append(target.id)
        if target.id == 2:
            raise RuntimeError("failed")
        return target.id

    results = run_selected_actions(targets(3), worker, continue_on_error=continue_on_error)
    assert writes == ([1, 2, 3] if continue_on_error else [1, 2])
    assert [result.action for result in results] == [
        "completed",
        "failed",
        "completed" if continue_on_error else "skipped",
    ]
    assert results[0].result == 1


def test_failure_preserves_in_flight_outcomes_and_never_starts_remaining() -> None:
    barrier = Barrier(2)
    failure_collected = Event()
    writes: list[int] = []
    lock = Lock()

    def worker(target: SelectedResource) -> int:
        with lock:
            writes.append(target.id)
        barrier.wait(timeout=2)
        if target.id == 1:
            raise RuntimeError("failed")
        assert failure_collected.wait(timeout=2)
        return target.id

    def describe(exc: Exception, target: SelectedResource) -> str:
        failure_collected.set()
        return str(exc)

    results = run_selected_actions(targets(5), worker, parallel=2, error_detail=describe)
    assert sorted(writes) == [1, 2]
    assert [result.action for result in results] == [
        "failed",
        "completed",
        "skipped",
        "skipped",
        "skipped",
    ]
    assert results[1].result == 2


def test_parallelism_is_capped_at_ten() -> None:
    barrier = Barrier(10)
    active = 0
    maximum = 0
    lock = Lock()

    def worker(target: SelectedResource) -> Any:
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        barrier.wait(timeout=2)
        with lock:
            active -= 1
        return target.id

    results = run_selected_actions(targets(20), worker, parallel=100)
    assert maximum == 10
    assert all(result.action == "completed" for result in results)
