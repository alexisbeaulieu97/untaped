"""Unit tests for ``cli.resource_commands._drain_parallel_with_worker``.

The helper owns the shared parallel-monitor scaffolding (executor lifecycle,
launch-order result collection, worker-boundary exception wrap) that
``_drain_parallel`` and ``_wait_parallel`` both consumed in duplicated form
before issue #140. Tested directly with stub ``worker_fn`` so the assertions
ride on pure logic without ``StreamJobEvents`` / ``WatchJob`` / ``Console``
plumbing — the through-CLI integration coverage stays in
``tests/integration/test_jobs_cli.py``.
"""

from __future__ import annotations

import threading
import time

from untaped_awx.cli.resource_commands import _drain_parallel_with_worker
from untaped_awx.domain import Job
from untaped_core import UntapedError


def _job(jid: int) -> Job:
    return Job(id=jid, kind="job", status="successful")


def test_collects_results_in_launch_order_even_when_workers_finish_out_of_order() -> None:
    """The helper walks ``futures`` in submission order before calling
    ``result()``, so a slow first worker still appears first in the
    returned ``results`` list. This pins the stable-stderr-row guarantee
    the CLI depends on."""
    jobs = [("deploy-a", _job(1)), ("deploy-b", _job(2))]

    def worker(name: str, job: Job) -> Job:
        # First-launched (deploy-a) sleeps longer so future-completion
        # order != launch order. Result list must still match launch
        # order.
        if name == "deploy-a":
            time.sleep(0.05)
        return job

    results, errors = _drain_parallel_with_worker(jobs, worker)

    assert [j.id for j in results] == [1, 2]
    assert errors == []


def test_untaped_error_from_worker_lands_in_errors_with_matching_name() -> None:
    """``UntapedError`` raised inside a worker is captured as
    ``(name, error)`` and does not abort the other workers."""
    jobs = [("deploy-a", _job(1)), ("deploy-b", _job(2))]
    sentinel = UntapedError("api 500")

    def worker(name: str, job: Job) -> Job:
        if name == "deploy-a":
            raise sentinel
        return job

    results, errors = _drain_parallel_with_worker(jobs, worker)

    assert [j.id for j in results] == [2]
    assert len(errors) == 1
    failed_name, failed_exc = errors[0]
    assert failed_name == "deploy-a"
    assert failed_exc is sentinel


def test_non_untaped_exception_is_wrapped_with_class_name_prefix() -> None:
    """Non-``UntapedError`` exceptions are wrapped at the worker
    boundary as ``UntapedError(f"{type(exc).__name__}: {exc}")``. Pins
    the format so the caller's ``error: <name>: <wrapped>`` row reads
    as ``error: deploy-a: RuntimeError: boom`` (single-prefix, with the
    original class name preserved for debuggability)."""
    jobs = [("deploy-a", _job(1))]

    def worker(_name: str, _job: Job) -> Job:
        raise RuntimeError("boom")

    results, errors = _drain_parallel_with_worker(jobs, worker)

    assert results == []
    assert len(errors) == 1
    failed_name, wrapped = errors[0]
    assert failed_name == "deploy-a"
    assert isinstance(wrapped, UntapedError)
    assert str(wrapped) == "RuntimeError: boom"
    # ``raise ... from exc`` preserves the original for tracebacks.
    assert isinstance(wrapped.__cause__, RuntimeError)


def test_while_running_callback_runs_between_submit_and_collect() -> None:
    """The optional ``while_running`` callable runs on the main thread
    *while* workers are pending — that's the seam ``_drain_parallel``
    uses to drain its event queue without blocking on
    ``future.result()`` first. Pin: callback observes the worker as
    in-flight before the worker completes."""
    started = threading.Event()
    release = threading.Event()
    observed_started = False
    observed_release_before_result = False

    def worker(_name: str, job: Job) -> Job:
        started.set()
        # Wait for the main thread (running ``while_running``) to
        # release us before returning — proves the helper hasn't
        # blocked on ``result()`` yet.
        release.wait(timeout=2)
        return job

    def drain() -> None:
        nonlocal observed_started, observed_release_before_result
        observed_started = started.wait(timeout=2)
        observed_release_before_result = True
        release.set()

    results, errors = _drain_parallel_with_worker(
        [("deploy-a", _job(1))], worker, while_running=drain
    )

    assert observed_started, "worker did not start before while_running ran"
    assert observed_release_before_result, "while_running did not run before result collection"
    assert [j.id for j in results] == [1]
    assert errors == []
