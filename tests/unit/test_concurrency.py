"""Behavioral tests for bounded_map (lifted from the ansible canonical form)."""

import contextvars
import threading
import time

import pytest

from untaped.concurrency import bounded_map


def test_serial_fast_path_preserves_input_order() -> None:
    seen: list[tuple[int, int]] = []
    bounded_map(
        lambda x: x * 10, [1, 2, 3], concurrency=1, on_each=lambda i, r: seen.append((i, r))
    )
    assert seen == [(1, 10), (2, 20), (3, 30)]


def test_single_item_runs_serially_even_with_high_concurrency() -> None:
    seen: list[int] = []
    bounded_map(lambda x: x, [7], concurrency=8, on_each=lambda i, r: seen.append(r))
    assert seen == [7]


def test_parallel_completes_all_items() -> None:
    seen: set[int] = set()
    bounded_map(lambda x: x * 2, list(range(20)), concurrency=4, on_each=lambda i, r: seen.add(r))
    assert seen == {x * 2 for x in range(20)}


def test_on_each_runs_on_the_calling_thread() -> None:
    caller = threading.get_ident()
    threads: set[int] = set()
    bounded_map(
        lambda x: x,
        list(range(8)),
        concurrency=4,
        on_each=lambda i, r: threads.add(threading.get_ident()),
    )
    assert threads == {caller}


def test_worker_exception_propagates() -> None:
    def boom(x: int) -> int:
        if x == 3:
            raise RuntimeError("worker failed")
        return x

    with pytest.raises(RuntimeError, match="worker failed"):
        bounded_map(boom, [1, 2, 3, 4], concurrency=2, on_each=lambda i, r: None)


def test_worker_sees_caller_contextvars() -> None:
    marker = contextvars.ContextVar("marker", default="missing")
    marker.set("caller")
    seen: list[str] = []

    bounded_map(lambda x: marker.get(), [1, 2], concurrency=2, on_each=lambda i, r: seen.append(r))

    assert seen == ["caller", "caller"]


@pytest.mark.parametrize("items", [[], [1]])
def test_concurrency_must_be_positive_before_fast_path(items: list[int]) -> None:
    with pytest.raises(ValueError, match="concurrency must be positive"):
        bounded_map(lambda x: x, items, concurrency=0, on_each=lambda i, r: None)


def test_on_abort_runs_before_waiting_for_in_flight_work() -> None:
    """``on_abort`` can release blocked workers; otherwise shutdown would hang."""
    release = threading.Event()

    def work(item: int) -> int:
        if item != 0:
            release.wait(timeout=10)
        return item

    def interrupt(_item: int, _result: int) -> None:
        raise KeyboardInterrupt

    start = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        bounded_map(work, [0, 1, 2], concurrency=3, on_each=interrupt, on_abort=release.set)
    assert release.is_set()
    assert time.monotonic() - start < 5


def test_on_abort_runs_on_serial_path() -> None:
    aborted: list[bool] = []

    def boom(_item: int) -> int:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        bounded_map(
            boom,
            [1],
            concurrency=1,
            on_each=lambda i, r: None,
            on_abort=lambda: aborted.append(True),
        )
    assert aborted == [True]


def test_on_abort_not_called_on_success() -> None:
    aborted: list[bool] = []
    bounded_map(
        lambda x: x,
        [1, 2, 3],
        concurrency=2,
        on_each=lambda i, r: None,
        on_abort=lambda: aborted.append(True),
    )
    assert aborted == []


def test_while_running_overlaps_a_single_item_before_results_are_consumed() -> None:
    """The hook sees the lone item in flight (a worker thread) and releases it."""
    caller = threading.get_ident()
    started = threading.Event()
    release = threading.Event()
    worker_threads: list[int] = []
    order: list[str] = []

    def work(x: int) -> int:
        worker_threads.append(threading.get_ident())
        started.set()
        assert release.wait(2)
        return x

    def foreground() -> None:
        assert started.wait(2)
        order.append("while_running")
        release.set()

    bounded_map(
        work,
        [1],
        concurrency=1,
        on_each=lambda i, r: order.append("on_each"),
        while_running=foreground,
    )
    assert order == ["while_running", "on_each"]
    assert worker_threads and worker_threads[0] != caller


def test_while_running_with_no_items_still_runs() -> None:
    ran: list[bool] = []
    bounded_map(
        lambda x: x,
        [],
        concurrency=2,
        on_each=lambda i, r: None,
        while_running=lambda: ran.append(True),
    )
    assert ran == [True]
