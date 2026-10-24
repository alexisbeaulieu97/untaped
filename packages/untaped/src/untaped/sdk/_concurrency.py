"""Bounded thread-pool mapping for standalone parallel work.

Stdlib-only. ``resolve_each``/``batch_apply`` stay sequential by design —
this is the standalone primitive for callers that manage their own loop.
:func:`bounded_iter` is the same work as a generator, behind
``Parallel.map``; :func:`bounded_map` is its 11.x callback form.
"""

from __future__ import annotations

import contextvars
import logging
from collections.abc import Callable, Iterable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed


def bounded_map[ItemT, ResultT](
    fn: Callable[[ItemT], ResultT],
    items: Sequence[ItemT],
    *,
    concurrency: int,
    on_each: Callable[[ItemT, ResultT], None],
    on_abort: Callable[[], None] | None = None,
    while_running: Callable[[], None] | None = None,
) -> None:
    """Apply ``fn`` to every item using at most ``concurrency`` worker threads.

    ``on_each(item, result)`` runs only on the calling thread: in input order
    when running serially (``len(items) <= 1`` or ``concurrency == 1``),
    otherwise in completion order. Exceptions raised by ``fn`` propagate to
    the caller from the consume loop. When anything escapes that loop —
    including ``KeyboardInterrupt`` — queued-but-unstarted work is cancelled
    instead of drained, so Ctrl-C stops large runs promptly rather than
    hanging while the executor's default shutdown runs every queued task.
    ``on_abort`` (optional) then runs on the calling thread *before* waiting
    for in-flight calls, so callers can stop them (e.g. kill child processes)
    instead of blocking until they finish on their own.

    ``while_running`` (optional) runs on the calling thread once every item
    is submitted and before any result is consumed, so the caller can do
    foreground work (e.g. drain a queue the workers feed) while they run.
    Supplying it always uses worker threads, even for a single item.
    """
    if concurrency < 1:
        raise ValueError("concurrency must be positive")
    if while_running is None and (len(items) <= 1 or concurrency == 1):
        try:
            for item in items:
                on_each(item, fn(item))
        except BaseException:
            if on_abort is not None:
                on_abort()
            raise
        return
    with ThreadPoolExecutor(max_workers=max(1, min(concurrency, len(items)))) as executor:
        try:
            futures = {
                executor.submit(contextvars.copy_context().run, fn, item): item for item in items
            }
            if while_running is not None:
                while_running()
            for future in as_completed(futures):
                on_each(futures[future], future.result())
        except BaseException:
            executor.shutdown(wait=False, cancel_futures=True)
            if on_abort is not None:
                on_abort()
            executor.shutdown(wait=True)
            raise


def bounded_iter[ItemT, ResultT](
    fn: Callable[[ItemT], ResultT],
    items: Iterable[ItemT],
    *,
    concurrency: int,
    ordered: bool = False,
    on_abort: Callable[[], None] | None = None,
    while_running: Callable[[], None] | None = None,
) -> Iterator[tuple[ItemT, ResultT]]:
    """Yield ``(item, fn(item))`` for every item, at most ``concurrency`` running at once.

    Pairs come in completion order, so a slow first item never holds back the
    rest, or in input order with ``ordered=True``. Serial (one item, or
    ``concurrency == 1``) without ``while_running``; otherwise each call runs
    in a worker thread with a copy of the caller's context. An exception from
    ``fn`` is raised from the generator. When the caller is interrupted
    (Ctrl-C), ``fn`` raises or the generator is closed (leaving a ``for``
    over it closes it once nothing else holds it; ``contextlib.closing``
    makes that explicit), queued work is cancelled and ``on_abort`` runs
    before waiting for the calls in flight, so it can stop them; the original
    exception wins over one ``on_abort`` raises. ``while_running`` runs on
    the caller's thread once every item is submitted, and the first pair (or
    a worker's exception) comes after it returns, so it must return on its
    own.
    """
    if concurrency < 1:
        raise ValueError("concurrency must be positive")
    work = list(items)
    if while_running is None and (len(work) <= 1 or concurrency == 1):
        try:
            for item in work:
                yield item, fn(item)
        except GeneratorExit:  # stopped between items: nothing is in flight
            raise
        except BaseException:
            _abort(on_abort)
            raise
        return
    executor = ThreadPoolExecutor(max_workers=max(1, min(concurrency, len(work))))
    try:
        futures = [executor.submit(contextvars.copy_context().run, fn, item) for item in work]
        index = {future: position for position, future in enumerate(futures)}
        if while_running is not None:
            while_running()
        for future in futures if ordered else as_completed(futures):
            yield work[index[future]], future.result()
    except BaseException:  # GeneratorExit too: the caller stopped
        executor.shutdown(wait=False, cancel_futures=True)
        _abort(on_abort)
        raise
    finally:
        executor.shutdown(wait=True)


def _abort(on_abort: Callable[[], None] | None) -> None:
    """Run ``on_abort`` while an exception unwinds; one it raises is logged, not raised."""
    if on_abort is None:
        return
    try:
        on_abort()
    except Exception:
        logging.getLogger("untaped.parallel").debug("on_abort failed", exc_info=True)
