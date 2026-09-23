"""Parallel monitor scaffolding shared by ``--track`` and ``--wait``.

Owns the executor / future-collection / error-wrap shape that both
``_drain_parallel`` (``--track``) and ``_wait_parallel`` (``--wait``)
need; each caller contributes only its unique mechanics (queue + print
loop for track; ``WatchJob`` lambda for wait).
"""

import queue
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from rich.console import Console
from rich.text import Text

from untaped.api import UntapedError
from untaped.capabilities.awx.application import WatchJob
from untaped.capabilities.awx.application.ports import JobMonitor, RawHttpResourceClient
from untaped.capabilities.awx.cli._event_render import render_event_text
from untaped.capabilities.awx.domain import Job, JobEvent
from untaped.capabilities.awx.domain.job import JOB_ROUTES


def _drain_parallel_with_worker(
    jobs: list[tuple[str, Job]],
    worker_fn: Callable[[str, Job], Job],
    *,
    while_running: Callable[[], None] | None = None,
    stop: threading.Event | None = None,
    finished: dict[str, Job] | None = None,
) -> tuple[list[Job], list[tuple[str, UntapedError]]]:
    """Run ``worker_fn(name, job)`` concurrently and collect outcomes in
    launch order.

    ``finished``, if given, receives each worker's final :class:`Job` as
    soon as it returns, so an interrupted caller knows which executions
    already ended.

    ``UntapedError`` raised by ``worker_fn`` is captured into
    ``errors``; any other ``Exception`` is wrapped at the worker
    boundary as ``UntapedError("<ClassName>: <message>")``.

    ``while_running``, if given, runs on the main thread between
    ``pool.submit`` and result-collection — the seam a caller needs to
    interleave foreground work with the still-pending pool, before
    ``future.result()`` would block. It runs inside the same ``with``
    block, so a raise still triggers ``shutdown(wait=True)``.

    On ``KeyboardInterrupt`` the ``stop`` event is set (workers poll via a
    stop-aware sleep and return promptly) and not-yet-started futures are
    cancelled before the executor joins, so Ctrl-C does not block until
    every execution reaches a terminal state.
    """

    def _wrap(name: str, job: Job) -> Job:
        # Catch ``Exception`` (not ``BaseException``) so ``KeyboardInterrupt``
        # propagates to the main thread for the executor's ``shutdown(wait=True)``
        # to cancel pending work cleanly. Widening this clause swallows Ctrl-C.
        try:
            result = worker_fn(name, job)
        except UntapedError:
            raise
        except Exception as exc:
            raise UntapedError(f"{type(exc).__name__}: {exc}") from exc
        if finished is not None:
            finished[name] = result
        return result

    with ThreadPoolExecutor(max_workers=min(10, len(jobs))) as pool:
        futures = [(name, pool.submit(_wrap, name, job)) for name, job in jobs]
        results: list[Job] = []
        errors: list[tuple[str, UntapedError]] = []
        try:
            if while_running is not None:
                while_running()
            for name, future in futures:
                try:
                    results.append(future.result())
                except UntapedError as exc:
                    errors.append((name, exc))
        except KeyboardInterrupt:
            if stop is not None:
                stop.set()
            for _name, future in futures:
                future.cancel()
            raise
    return results, errors


def _drain_parallel(
    monitor: JobMonitor,
    jobs: list[tuple[str, Job]],
    console: Console,
    *,
    stop: threading.Event | None = None,
    finished: dict[str, Job] | None = None,
) -> tuple[list[Job], list[tuple[str, UntapedError]]]:
    """Drain ``--track`` events from multiple jobs concurrently.

    Workers stream structured events, or workflow status changes, onto a queue; the
    main thread drains the queue and prints with the originating
    template name as a prefix so concurrent output stays
    disambiguable on a shared stderr. After every worker has signalled
    completion (sentinel ``(name, None)``), each future's final
    :class:`Job` (post ``monitor.fetch``) is collected in launch order
    by :func:`_drain_parallel_with_worker` so the caller's per-job
    error stderr rows + ``any_failed`` exit-code semantics stay stable.

    ``Ctrl-C`` sets ``stop``; a monitor built with a stop-aware sleep
    (the CLI context's) then ends its polling loop immediately.
    """
    q: queue.Queue[tuple[str, JobEvent | Job | None]] = queue.Queue()

    def _worker(name: str, job: Job) -> Job:
        # Sentinel pushed in ``finally`` *before* ``monitor.fetch`` so
        # a slow or failing fetch never blocks the main thread's queue
        # drain.
        try:
            if JOB_ROUTES[job.kind].events is None:
                final = job
                for status in monitor.stream_status(job):
                    q.put((name, status))
                    final = status
                return final
            else:
                for ev in monitor.stream_events(job, follow=True):
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
            if isinstance(ev, Job):
                console.print(Text(f"[{name}] {ev.kind}#{ev.id}: {ev.status}"))
            else:
                console.print(render_event_text(ev, prefix=name))

    return _drain_parallel_with_worker(
        jobs, _worker, while_running=_drain_queue, stop=stop, finished=finished
    )


def _wait_parallel(
    client: RawHttpResourceClient,
    jobs: list[tuple[str, Job]],
    *,
    sleep: Callable[[float], None] | None = None,
    stop: threading.Event | None = None,
    finished: dict[str, Job] | None = None,
) -> tuple[list[Job], list[tuple[str, UntapedError]]]:
    """Block-wait on multiple jobs concurrently — no streaming.

    Mirrors :func:`_drain_parallel` for the ``--wait`` (no
    ``--track``) path: each worker calls ``WatchJob(client)(job)``
    until the job hits a terminal state and returns. The
    executor / collection / error-wrap scaffolding lives in
    :func:`_drain_parallel_with_worker`.
    """
    watch = WatchJob(client, sleep=sleep) if sleep is not None else WatchJob(client)
    return _drain_parallel_with_worker(
        jobs, lambda _name, job: watch(job), stop=stop, finished=finished
    )
