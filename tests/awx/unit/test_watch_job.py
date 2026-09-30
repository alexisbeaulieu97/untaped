"""Unit tests for the ``WatchJob`` use case."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

import untaped.capabilities.awx.domain.job as job_module
from untaped.capabilities.awx.application import WatchJob
from untaped.capabilities.awx.application.ports import RawHttpResourceClient
from untaped.capabilities.awx.domain import Job


class _StubClient:
    """Minimal stub covering the raw-HTTP ``request`` port.

    ``WatchJob`` polls via ``request("GET", "<api_path>/<job_id>/")`` —
    other ``ResourceClient`` methods aren't touched.
    """

    def __init__(self, *, request_results: list[dict[str, Any]]) -> None:
        self._request_results = request_results
        self.calls: list[tuple[str, str]] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        idx = len(self.calls)
        self.calls.append((method, path))
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


def test_watch_job_respects_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = iter([10.0, 10.0, 11.0, 12.0])
    monkeypatch.setattr(job_module, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    client = _StubClient(
        request_results=[{"id": 1, "status": "running"}, {"id": 1, "status": "waiting"}]
    )
    sleeps: list[float] = []
    use = WatchJob(cast(RawHttpResourceClient, client), sleep=sleeps.append, poll_interval=1.0)
    final = use(Job(id=1, kind="job", status="pending"), timeout=2.0)
    assert final.status == "waiting"
    assert sleeps == [1.0, 1.0]
    assert client.calls == [("GET", "jobs/1/"), ("GET", "jobs/1/")]


def test_watch_job_zero_timeout_does_not_poll() -> None:
    client = _StubClient(request_results=[])
    sleeps: list[float] = []
    use = WatchJob(cast(RawHttpResourceClient, client), sleep=sleeps.append)
    job = Job(id=1, kind="job", status="running")
    assert use(job, timeout=0.0) is job
    assert sleeps == []
    assert client.calls == []
