"""Bounded, dependency-aware scheduling of fixed AWX work items with stop-on-failure.

The one scheduler behind batch mutations and independent selected actions.
It runs on :func:`untaped.capability_api.bounded_map`, so at most ``parallel`` workers
run, each sees the caller's context variables (``--quiet`` reaches them),
and Ctrl-C cancels work that has not started instead of draining it.

Items start in dependency order, lowest index first among ready items, so a
serial run is fully deterministic. A worker whose item still has unfinished
dependencies waits for them; dependencies always start earlier, so the wait
cannot deadlock. After :meth:`Schedule.stop`, items that have not started
get their ``skipped`` result instead of running; in-flight items finish.
"""

from __future__ import annotations

import heapq
import threading
from collections.abc import Callable, Sequence

from untaped.capability_api import UsageError, bounded_map

MAX_PARALLEL = 10
"""Upper bound on concurrent AWX requests for any batch."""


class ScheduleInterruptedError(KeyboardInterrupt):
    """Ctrl-C during a schedule; ``results`` holds every item that finished."""

    def __init__(self, results: dict[int, object]) -> None:
        super().__init__()
        self.results = results


class Schedule:
    """One bounded run; :meth:`stop` keeps not-yet-started items from running."""

    def __init__(self, *, parallel: int) -> None:
        if parallel < 1:
            raise UsageError("--parallel must be at least 1")
        self._parallel = min(parallel, MAX_PARALLEL)
        self._stop = threading.Event()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def stop(self) -> None:
        """Let in-flight items finish; every item not yet started is skipped."""
        self._stop.set()

    def run[R](
        self,
        count: int,
        work: Callable[[int], R],
        *,
        skipped: Callable[[int], R],
        dependencies: Sequence[Sequence[int]] | None = None,
        blocked: Callable[[int, list[R]], R | None] | None = None,
    ) -> dict[int, R]:
        """Run items ``0..count-1``; return each finished item's result by index.

        ``blocked(index, dependency_results)`` may return a result that
        replaces running the item (e.g. "a dependency failed"). Items caught
        in a dependency cycle never start and are absent from the result.
        On Ctrl-C, raises :class:`ScheduleInterruptedError` once in-flight items
        have finished.
        """
        deps = [tuple(item) for item in dependencies] if dependencies else [()] * count
        order = _dependency_order(deps)
        finished = {index: threading.Event() for index in order}
        results: dict[int, R] = {}
        interrupted = threading.Event()

        def task(index: int) -> None:
            try:
                for dependency in deps[index]:
                    finished[dependency].wait()
                if interrupted.is_set():
                    return
                if self.stopped:
                    results[index] = skipped(index)
                    return
                replacement = (
                    blocked(index, [results[dependency] for dependency in deps[index]])
                    if blocked is not None
                    else None
                )
                results[index] = work(index) if replacement is None else replacement
            except BaseException:
                # An interrupt raised by the item itself: start nothing else.
                interrupted.set()
                raise
            finally:
                finished[index].set()

        def abort() -> None:
            # Wake every dependency wait so in-flight workers can return.
            interrupted.set()
            for event in finished.values():
                event.set()

        try:
            bounded_map(
                task,
                order,
                concurrency=self._parallel,
                on_each=lambda _index, _result: None,
                on_abort=abort,
                # Always on worker threads (even serially): Ctrl-C then lands
                # in the main thread's wait, so in-flight requests finish.
                while_running=idle,
            )
        except KeyboardInterrupt:
            raise ScheduleInterruptedError(dict(results)) from None
        return results


def idle() -> None:
    """A no-op ``while_running`` that keeps even one item on a worker thread."""


def _dependency_order(dependencies: Sequence[Sequence[int]]) -> list[int]:
    """Kahn order preferring the lowest ready index; cyclic items are left out."""
    remaining = {index: set(deps) for index, deps in enumerate(dependencies)}
    dependents: dict[int, list[int]] = {}
    for index, deps in remaining.items():
        for dependency in deps:
            dependents.setdefault(dependency, []).append(index)
    ready = [index for index, deps in remaining.items() if not deps]
    heapq.heapify(ready)
    order: list[int] = []
    while ready:
        index = heapq.heappop(ready)
        order.append(index)
        for dependent in dependents.get(index, ()):
            remaining[dependent].discard(index)
            if not remaining[dependent]:
                heapq.heappush(ready, dependent)
    return order


__all__ = ["MAX_PARALLEL", "Schedule", "ScheduleInterruptedError"]
