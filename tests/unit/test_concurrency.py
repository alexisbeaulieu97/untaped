"""Behavioral tests for bounded_map (lifted from the ansible canonical form)."""

import threading

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
