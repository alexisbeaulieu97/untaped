"""Unit tests for the ``WatchJob`` use case."""

from __future__ import annotations

from typing import Any, cast

from untaped_awx.application import WatchJob
from untaped_awx.application.ports import RawHttpResourceClient
from untaped_awx.domain import Job


class _StubClient:
    """Minimal stub covering the raw-HTTP ``request`` port.

    ``WatchJob`` polls via ``request("GET", "<api_path>/<job_id>/")`` —
    other ``ResourceClient`` methods aren't touched.
    """

    def __init__(self, *, request_results: list[dict[str, Any]]) -> None:
        self._request_results = request_results
        self._request_calls = 0

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        idx = self._request_calls
        self._request_calls += 1
        return self._request_results[idx] if idx < len(self._request_results) else {}


def test_watch_job_polls_until_terminal() -> None:
    client = _StubClient(
        request_results=[
            {"id": 1, "status": "running"},
            {"id": 1, "status": "successful"},
        ]
    )
    sleeps: list[float] = []
    use = WatchJob(cast(RawHttpResourceClient, client), sleep=sleeps.append, poll_interval=0.0)
    job = Job(id=1, kind="job", status="running")
    final = use(job)
    assert final.status == "successful"
    assert len(sleeps) == 2  # two poll cycles


def test_watch_job_returns_immediately_if_terminal() -> None:
    client = _StubClient(request_results=[])
    sleeps: list[float] = []
    use = WatchJob(cast(RawHttpResourceClient, client), sleep=sleeps.append, poll_interval=0.0)
    job = Job(id=1, kind="job", status="successful")
    assert use(job) is job
    assert sleeps == []


def test_watch_job_respects_timeout() -> None:
    client = _StubClient(request_results=[{"id": 1, "status": "running"}] * 100)
    sleeps: list[float] = []
    use = WatchJob(cast(RawHttpResourceClient, client), sleep=sleeps.append, poll_interval=0.0)
    job = Job(id=1, kind="job", status="running")
    # zero timeout returns the input immediately
    final = use(job, timeout=0.0)
    assert final.status == "running"
