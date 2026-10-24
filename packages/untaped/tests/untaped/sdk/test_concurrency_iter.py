"""bounded_iter and Parallel.map: (item, result) pairs as the work finishes."""

from __future__ import annotations

import contextvars
import threading

import pytest

from untaped.sdk import Parallel
from untaped.sdk._concurrency import bounded_iter


def test_pairs_come_in_completion_order() -> None:
    first_done = threading.Event()

    def work(item: int) -> int:
        if item == 0:
            first_done.wait(5)  # the first item finishes last
        return item * 10

    pairs = []
    for item, result in bounded_iter(work, [0, 1, 2], concurrency=3):
        pairs.append((item, result))
        if len(pairs) == 2:
            first_done.set()
    assert pairs[-1] == (0, 0)
    assert sorted(pairs) == [(0, 0), (1, 10), (2, 20)]


def test_ordered_keeps_the_input_order() -> None:
    late = threading.Event()

    def work(item: int) -> int:
        if item == 0:
            late.wait(0.2)
        return item

    assert list(bounded_iter(work, [0, 1, 2], concurrency=3, ordered=True)) == [
        (0, 0),
        (1, 1),
        (2, 2),
    ]


def test_serial_without_workers_in_input_order() -> None:
    threads: set[int] = set()

    def work(item: int) -> int:
        threads.add(threading.get_ident())
        return item

    assert list(bounded_iter(work, iter([1, 2, 3]), concurrency=1)) == [(1, 1), (2, 2), (3, 3)]
    assert threads == {threading.get_ident()}


def test_an_exception_is_raised_and_on_abort_runs() -> None:
    aborted: list[str] = []

    def work(item: int) -> int:
        if item == 1:
            raise RuntimeError("boom")
        return item

    for concurrency in (1, 4):
        aborted.clear()
        with pytest.raises(RuntimeError, match="boom"):
            list(
                bounded_iter(
                    work, [0, 1], concurrency=concurrency, on_abort=lambda: aborted.append("x")
                )
            )
        assert aborted == ["x"], concurrency


def test_leaving_early_cancels_the_queue_and_aborts_the_work_in_flight() -> None:
    release = threading.Event()
    started: list[int] = []
    aborted: list[str] = []

    def work(item: int) -> int:
        started.append(item)
        if item:
            release.wait(5)
        return item

    def abort() -> None:
        aborted.append("x")
        release.set()

    pairs = bounded_iter(work, range(10), concurrency=2, on_abort=abort)
    assert next(pairs) == (0, 0)
    pairs.close()
    assert aborted == ["x"]
    assert len(started) < 10


def test_leaving_a_serial_run_between_items_aborts_nothing() -> None:
    aborted: list[str] = []
    pairs = bounded_iter(lambda x: x, [1, 2], concurrency=1, on_abort=lambda: aborted.append("x"))
    assert next(pairs) == (1, 1)
    pairs.close()
    assert aborted == []


def test_while_running_runs_before_the_first_pair() -> None:
    order: list[str] = []
    pairs = bounded_iter(
        lambda x: order.append("work") or x,
        [1],
        concurrency=1,
        while_running=lambda: order.append("foreground"),
    )
    assert list(pairs) == [(1, 1)]
    assert "foreground" in order


def test_workers_see_the_callers_context() -> None:
    var: contextvars.ContextVar[str] = contextvars.ContextVar("var")
    var.set("caller")
    assert {result for _, result in bounded_iter(lambda _: var.get(), [1, 2], concurrency=2)} == {
        "caller"
    }


def test_concurrency_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        list(bounded_iter(lambda x: x, [], concurrency=0))


def test_parallel_map_runs_its_value_at_a_time() -> None:
    running, most = 0, 0
    lock = threading.Lock()
    gate = threading.Barrier(2, timeout=5)

    def work(item: int) -> int:
        nonlocal running, most
        with lock:
            running += 1
            most = max(most, running)
        if item < 2:
            gate.wait()  # the first two run together
        with lock:
            running -= 1
        return item

    pairs = dict(Parallel(default=2).map(work, range(6)))
    assert pairs == {item: item for item in range(6)}
    assert most == 2
