"""Parallel monitor scaffolding shared by ``--track`` and ``--wait``.

Owns the executor / future-collection / error-wrap shape that both
``_drain_parallel`` (``--track``) and ``_wait_parallel`` (``--wait``)
need; each caller contributes only its unique mechanics (queue + print
loop for track; ``WatchJob`` lambda for wait).
"""

from __future__ import annotations

import queue
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from rich.console import Console
from untaped_core import UntapedError

from untaped_awx.application import StreamJobEvents, WatchJob
from untaped_awx.application.ports import JobMonitor, RawHttpResourceClient
from untaped_awx.cli._event_render import render_event_text
from untaped_awx.domain import Job, JobEvent


def _drain_parallel_with_worker(
    jobs: list[tuple[str, Job]],
    worker_fn: Callable[[str, Job], Job],
    *,
    while_running: Callable[[], None] | None = None,
) -> tuple[list[Job], list[tuple[str, UntapedError]]]:
    """Run ``worker_fn(name, job)`` concurrently and collect outcomes in
    launch order.

    ``UntapedError`` raised by ``worker_fn`` is captured into
    ``errors``; any other ``Exception`` is wrapped at the worker
    boundary as ``UntapedError("<ClassName>: <message>")``.

    ``while_running``, if given, runs on the main thread between
    ``pool.submit`` and result-collection — the seam a caller needs to
    interleave foreground work with the still-pending pool, before
    ``future.result()`` would block. It runs inside the same ``with``
    block, so a raise still triggers ``shutdown(wait=True)``.
    """

    def _wrap(name: str, job: Job) -> Job:
        # Catch ``Exception`` (not ``BaseException``) so ``KeyboardInterrupt``
        # propagates to the main thread for the executor's ``shutdown(wait=True)``
        # to cancel pending work cleanly. Widening this clause swallows Ctrl-C.
        try:
            return worker_fn(name, job)
        except UntapedError:
            raise
        except Exception as exc:
            raise UntapedError(f"{type(exc).__name__}: {exc}") from exc

    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = [(name, pool.submit(_wrap, name, job)) for name, job in jobs]
        if while_running is not None:
            while_running()
        results: list[Job] = []
        errors: list[tuple[str, UntapedError]] = []
        for name, future in futures:
            try:
                results.append(future.result())
            except UntapedError as exc:
                errors.append((name, exc))
    return results, errors


def _drain_parallel(
    monitor: JobMonitor,
    jobs: list[tuple[str, Job]],
    console: Console,
) -> tuple[list[Job], list[tuple[str, UntapedError]]]:
    """Drain ``--track`` events from multiple jobs concurrently.

    Workers stream :class:`JobEvent`s onto a :class:`queue.Queue`; the
    main thread drains the queue and prints with the originating
    template name as a prefix so concurrent output stays
    disambiguable on a shared stderr. After every worker has signalled
    completion (sentinel ``(name, None)``), each future's final
    :class:`Job` (post ``monitor.fetch``) is collected in launch order
    by :func:`_drain_parallel_with_worker` so the caller's per-job
    error stderr rows + ``any_failed`` exit-code semantics stay stable.

    Note: ``Ctrl-C`` may take up to one polling interval to abort
    because workers don't cooperatively cancel — the executor's
    ``shutdown(wait=True)`` blocks until each polling loop next
    iterates and the job goes terminal.
    """
    q: queue.Queue[tuple[str, JobEvent | None]] = queue.Queue()

    def _worker(name: str, job: Job) -> Job:
        # Sentinel pushed in ``finally`` *before* ``monitor.fetch`` so
        # a slow or failing fetch never blocks the main thread's queue
        # drain.
        try:
            for ev in StreamJobEvents(monitor)(job, follow=True):
                q.put((name, ev))
        finally:
            q.put((name, None))
        return monitor.fetch(job)

    def _drain_queue() -> None:
        # Single-threaded printing: queue drain runs only here so a
        # multi-segment Rich Text never interleaves between workers.
        done = 0
        while done < len(jobs):
            name, ev = q.get()
            if ev is None:
                done += 1
                continue
            console.print(render_event_text(ev, prefix=name))

    return _drain_parallel_with_worker(jobs, _worker, while_running=_drain_queue)


def _wait_parallel(
    client: RawHttpResourceClient,
    jobs: list[tuple[str, Job]],
) -> tuple[list[Job], list[tuple[str, UntapedError]]]:
    """Block-wait on multiple jobs concurrently — no streaming.

    Mirrors :func:`_drain_parallel` for the ``--wait`` (no
    ``--track``) path: each worker calls ``WatchJob(client)(job)``
    until the job hits a terminal state and returns. The
    executor / collection / error-wrap scaffolding lives in
    :func:`_drain_parallel_with_worker`.
    """
    watch = WatchJob(client)
    return _drain_parallel_with_worker(jobs, lambda _name, job: watch(job))


__all__ = ["_drain_parallel", "_drain_parallel_with_worker", "_wait_parallel"]
