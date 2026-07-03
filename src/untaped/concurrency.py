"""Bounded thread-pool mapping for standalone parallel work.

Stdlib-only. ``resolve_each``/``batch_apply`` stay sequential by design —
this is the standalone primitive for callers that manage their own loop.
"""

from __future__ import annotations

import contextvars
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed


def bounded_map[ItemT, ResultT](
    fn: Callable[[ItemT], ResultT],
    items: Sequence[ItemT],
    *,
    concurrency: int,
    on_each: Callable[[ItemT, ResultT], None],
) -> None:
    """Apply ``fn`` to every item using at most ``concurrency`` worker threads.

    ``on_each(item, result)`` runs only on the calling thread: in input order
    when running serially (``len(items) <= 1`` or ``concurrency == 1``),
    otherwise in completion order. Exceptions raised by ``fn`` propagate to
    the caller from the consume loop. When anything escapes that loop —
    including ``KeyboardInterrupt`` — queued-but-unstarted work is cancelled
    instead of drained, so Ctrl-C stops large runs promptly rather than
    hanging while the executor's default shutdown runs every queued task.
    """
    if concurrency < 1:
        raise ValueError("concurrency must be positive")
    if len(items) <= 1 or concurrency == 1:
        for item in items:
            on_each(item, fn(item))
        return
    with ThreadPoolExecutor(max_workers=min(concurrency, len(items))) as executor:
        try:
            futures = {
                executor.submit(contextvars.copy_context().run, fn, item): item for item in items
            }
            for future in as_completed(futures):
                on_each(futures[future], future.result())
        except BaseException:
            executor.shutdown(cancel_futures=True)
            raise
