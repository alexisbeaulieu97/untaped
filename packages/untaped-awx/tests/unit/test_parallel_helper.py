"""Direct unit pin for ``_drain_parallel_with_worker``'s ``while_running`` seam.

Most of the helper's behaviour (launch-order collection, ``UntapedError``
capture, ``"<ClassName>: <message>"`` wrap format) is covered through the
public CLI in ``tests/integration/test_jobs_cli.py`` — that's the right
home for those assertions per AGENTS.md ("Test through public APIs").

What integration tests *cannot* observe is the timing seam: that
``while_running`` runs on the main thread *between* ``pool.submit`` and
``future.result()``-collection, so a caller can drain a shared queue
while workers are still pending. If a refactor accidentally moved
``while_running()`` after the collection loop, every existing
``--track`` integration test would still pass — events would still
print, just at the end — yet the helper's documented contract would be
silently broken. This one stub-driven test pins that invariant.
"""

from __future__ import annotations

import threading

from untaped_awx.cli._parallel import _drain_parallel_with_worker
from untaped_awx.domain import Job


def _job(jid: int) -> Job:
    return Job(id=jid, kind="job", status="successful")


def test_while_running_callback_runs_between_submit_and_collect() -> None:
    """``while_running`` observes the worker as *in-flight* and is the
    one that releases it — proves the helper hasn't blocked on
    ``future.result()`` yet."""
    started = threading.Event()
    release = threading.Event()
    observed_started = False
    observed_release_before_result = False

    def worker(_name: str, job: Job) -> Job:
        started.set()
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
