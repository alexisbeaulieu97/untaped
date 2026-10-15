"""Independent action scheduling preserves bounded in-flight and skipped outcomes."""

from threading import Barrier, Event, Lock
from typing import Any

import pytest

from untaped_awx.application.selected_actions import run_selected_actions
from untaped_awx.application.selection import SelectedResource


def targets(count: int) -> list[SelectedResource]:
    return [
        SelectedResource("Project", index, str(index), {}, {"id": index})
        for index in range(1, count + 1)
    ]


@pytest.mark.parametrize("fail_fast", [False, True])
def test_serial_stops_after_failure_only_when_fail_fast(fail_fast: bool) -> None:
    writes: list[int] = []

    def worker(target: SelectedResource) -> int:
        writes.append(target.id)
        if target.id == 2:
            raise RuntimeError("failed")
        return target.id

    results = run_selected_actions(targets(3), worker, fail_fast=fail_fast)
    assert writes == ([1, 2] if fail_fast else [1, 2, 3])
    assert [result.action for result in results] == [
        "completed",
        "failed",
        "skipped" if fail_fast else "completed",
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

    results = run_selected_actions(
        targets(5), worker, parallel=2, fail_fast=True, error_detail=describe
    )
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


def test_a_rejected_token_stops_the_rest_without_fail_fast() -> None:
    from untaped.sdk import ConfigError

    writes: list[int] = []

    def worker(target: SelectedResource) -> int:
        writes.append(target.id)
        if target.id == 1:
            raise ConfigError("AWX rejected the token (HTTP 401)")
        return target.id

    results = run_selected_actions(targets(3), worker)
    assert writes == [1]
    assert [result.action for result in results] == ["failed", "skipped", "skipped"]
