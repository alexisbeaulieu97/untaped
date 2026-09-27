"""Parallel monitor scaffolding shared by ``--follow`` and ``--wait``.

Owns the bounded-worker / launch-order collection / error-wrap shape that
both ``drain_parallel`` (``--follow``) and ``wait_parallel`` (``--wait``)
need, on top of :func:`untaped.capability_api.bounded_map`; each caller contributes
only its unique mechanics (queue + print loop for follow; ``WatchJob``
lambda for wait).
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable

from rich.text import Text

from untaped.capabilities.awx.application import WatchJob
from untaped.capabilities.awx.application.ports import JobMonitor, RawHttpResourceClient
from untaped.capabilities.awx.application.scheduling import MAX_PARALLEL, idle
from untaped.capabilities.awx.domain import Job
from untaped.capabilities.awx.domain.job import JOB_ROUTES
from untaped.capability_api import UntapedError, bounded_map


def drain_parallel_with_worker(
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

    ``while_running``, if given, runs on the main thread once every job is
    submitted and before results are collected — the seam a caller needs to
    interleave foreground work with the still-pending workers.

    On ``KeyboardInterrupt`` the ``stop`` event is set (workers poll via a
    stop-aware sleep and return promptly) and not-yet-started jobs are
    cancelled before the workers are joined, so Ctrl-C does not block until
    every execution reaches a terminal state.
    """
    outcomes: dict[int, Job | UntapedError] = {}

    def _wrap(index: int) -> Job | UntapedError:
        # Catch ``Exception`` (not ``BaseException``) so ``KeyboardInterrupt``
        # still reaches the main thread's cancellation path. Widening this
        # clause swallows Ctrl-C.
        name, job = jobs[index]
        try:
            result = worker_fn(name, job)
        except UntapedError as exc:
            return exc
        except Exception as exc:
            return UntapedError(f"{type(exc).__name__}: {exc}")
        if finished is not None:
            finished[name] = result
        return result

    bounded_map(
        _wrap,
        range(len(jobs)),
        concurrency=MAX_PARALLEL,
        on_each=outcomes.__setitem__,
        on_abort=stop.set if stop is not None else None,
        # Always on worker threads (even one job): Ctrl-C then lands in the
        # main thread's wait, never inside a worker's HTTP call.
        while_running=while_running or idle,
    )
    results: list[Job] = []
    errors: list[tuple[str, UntapedError]] = []
    for index, (name, _job) in enumerate(jobs):
        outcome = outcomes[index]
        if isinstance(outcome, UntapedError):
            errors.append((name, outcome))
        else:
            results.append(outcome)
    return results, errors


def drain_parallel(
    monitor: JobMonitor,
    jobs: list[tuple[str, Job]],
    write: Callable[[Text], None],
    *,
    stop: threading.Event | None = None,
    finished: dict[str, Job] | None = None,
) -> tuple[list[Job], list[tuple[str, UntapedError]]]:
    """Stream ``--follow`` logs from multiple jobs concurrently.

    Workers stream each job's stdout, or a workflow job's status changes,
    onto a queue; the main thread drains the queue and hands each line to
    ``write`` (``ui.styled`` in the CLI). With several jobs every line is
    prefixed with its label so concurrent logs stay disambiguable on a
    shared stderr; a single job's log reads as ``ansible-playbook`` prints
    it, ending with its PLAY RECAP. After every worker has signalled
    completion (sentinel ``(name, None)``), each future's final
    :class:`Job` (post ``monitor.fetch``) is collected in launch order
    by :func:`drain_parallel_with_worker` so the caller's per-job
    error stderr rows + ``any_failed`` exit-code semantics stay stable.

    ``Ctrl-C`` sets ``stop``; a monitor built with a stop-aware sleep
    (the CLI context's) then ends its polling loop immediately.
    """
    q: queue.Queue[tuple[str, str | Job | None]] = queue.Queue()
    prefix = len(jobs) > 1

    def _worker(name: str, job: Job) -> Job:
        # Sentinel pushed in ``finally`` *before* ``monitor.fetch`` so
        # a slow or failing fetch never blocks the main thread's queue
        # drain.
        try:
            if not JOB_ROUTES[job.kind].stdout:
                final = job
                for status in monitor.stream_status(job):
                    q.put((name, status))
                    final = status
                return final
            else:
                for line in monitor.stream_stdout(job):
                    q.put((name, line))
        finally:
            q.put((name, None))
        return monitor.fetch(job)

    def _drain_queue() -> None:
        # Single-threaded printing: queue drain runs only here so a
        # multi-segment Rich Text never interleaves between workers.
        done = 0
        while done < len(jobs):
            name, item = q.get()
            if item is None:
                done += 1
                continue
            line = f"{item.kind}#{item.id}: {item.status}" if isinstance(item, Job) else item
            if prefix:
                write(Text.assemble(Text(f"[{name}] ", style="dim cyan"), line))
            else:
                write(Text(line))

    return drain_parallel_with_worker(
        jobs, _worker, while_running=_drain_queue, stop=stop, finished=finished
    )


def wait_parallel(
    client: RawHttpResourceClient,
    jobs: list[tuple[str, Job]],
    *,
    sleep: Callable[[float], None] | None = None,
    stop: threading.Event | None = None,
    finished: dict[str, Job] | None = None,
    timeout: float | None = None,
) -> tuple[list[Job], list[tuple[str, UntapedError]]]:
    """Block-wait on multiple jobs concurrently — no streaming.

    Mirrors :func:`drain_parallel` for the ``--wait`` (no
    ``--follow``) path: each worker calls ``WatchJob(client)(job)``
    until the job hits a terminal state and returns. The
    executor / collection / error-wrap scaffolding lives in
    :func:`drain_parallel_with_worker`. ``timeout`` bounds each wait;
    a job still running then comes back in its last polled state.
    """
    watch = WatchJob(client, sleep=sleep) if sleep is not None else WatchJob(client)
    return drain_parallel_with_worker(
        jobs, lambda _name, job: watch(job, timeout=timeout), stop=stop, finished=finished
    )
