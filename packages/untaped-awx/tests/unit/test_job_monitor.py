"""Unit tests for :class:`PollingJobMonitor`.

Stubs the :class:`ResourceClient` so we can exercise the polling /
pagination / terminal-detection logic without a real httpx round trip.
The fake client is queue-driven: each test scripts what the next
``request`` / ``request_text`` call should return, and the monitor
walks the script until done.
"""

from __future__ import annotations

from typing import Any, cast

from untaped_awx.application.ports import RawHttpResourceClient
from untaped_awx.domain import Job
from untaped_awx.infrastructure.job_monitor import PollingJobMonitor


class _FakeClient:
    """Records requests and returns scripted responses."""

    def __init__(
        self,
        *,
        json_responses: list[dict[str, Any]] | None = None,
        text_responses: list[str] | None = None,
    ) -> None:
        self._json = list(json_responses or [])
        self._text = list(text_responses or [])
        self.json_calls: list[tuple[str, str, dict[str, str]]] = []
        self.text_calls: list[tuple[str, str, dict[str, str]]] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.json_calls.append((method, path, dict(params or {})))
        if not self._json:
            return {"results": [], "next": None}
        return self._json.pop(0)

    def request_text(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
    ) -> str:
        self.text_calls.append((method, path, dict(params or {})))
        if not self._text:
            return ""
        return self._text.pop(0)


def _running(id_: int = 7) -> Job:
    return Job(id=id_, kind="job", status="running")


def _terminal_record(id_: int = 7, status: str = "successful") -> dict[str, Any]:
    return {"id": id_, "status": status}


def test_fetch_returns_job_with_kind_preserved() -> None:
    client = _FakeClient(json_responses=[_terminal_record(status="successful")])
    monitor = PollingJobMonitor(cast(RawHttpResourceClient, client), sleep=lambda _: None)
    job = monitor.fetch(_running())
    assert job.id == 7
    assert job.status == "successful"
    assert job.kind == "job"
    assert client.json_calls == [("GET", "jobs/7/", {})]


def test_fetch_uses_kind_specific_api_path_for_workflow_jobs() -> None:
    client = _FakeClient(json_responses=[{"id": 9, "status": "running"}])
    monitor = PollingJobMonitor(cast(RawHttpResourceClient, client), sleep=lambda _: None)
    monitor.fetch(Job(id=9, kind="workflow_job", status="running"))
    assert client.json_calls[0][1] == "workflow_jobs/9/"


def test_fetch_stdout_passes_start_line_param() -> None:
    client = _FakeClient(text_responses=["line-3\nline-4\n"])
    monitor = PollingJobMonitor(cast(RawHttpResourceClient, client), sleep=lambda _: None)
    lines = monitor.fetch_stdout(_running(), start_line=2)
    assert lines == ["line-3", "line-4"]
    method, path, params = client.text_calls[0]
    assert method == "GET"
    assert path == "jobs/7/stdout/"
    assert params == {"format": "txt", "start_line": "2"}


def test_stream_stdout_polls_until_terminal_then_drains() -> None:
    """Two text-poll cycles: first while running, second after terminal."""
    client = _FakeClient(
        text_responses=["a\nb\n", "c\nd\n"],
        json_responses=[_terminal_record(status="successful")],
    )
    sleeps: list[float] = []
    monitor = PollingJobMonitor(cast(RawHttpResourceClient, client), sleep=sleeps.append)
    lines = list(monitor.stream_stdout(_running()))
    assert lines == ["a", "b", "c", "d"]
    # First text call had cursor 0; second had cursor 2 (after the 2 lines we got).
    assert client.text_calls[0][2]["start_line"] == "0"
    assert client.text_calls[1][2]["start_line"] == "2"
    # We slept exactly once between the two polls.
    assert sleeps == [2.0]


def test_stream_events_yields_until_terminal_and_advances_counter() -> None:
    """Two event-poll cycles, second after the job flips to ``successful``."""
    page_1 = {
        "results": [
            {"counter": 1, "event": "playbook_on_play_start", "play": "Deploy"},
            {"counter": 2, "event": "playbook_on_task_start", "task": "install"},
        ],
        "next": None,
    }
    page_2 = {
        "results": [
            {"counter": 3, "event": "runner_on_ok", "host": 5, "host_name": "web-01"},
        ],
        "next": None,
    }
    client = _FakeClient(
        json_responses=[
            page_1,
            _terminal_record(status="successful"),  # post-page-1 fetch()
            page_2,
        ]
    )
    monitor = PollingJobMonitor(cast(RawHttpResourceClient, client), sleep=lambda _: None)
    events = list(monitor.stream_events(_running()))
    counters = [ev.counter for ev in events]
    assert counters == [1, 2, 3]
    # counter__gt advances through the cycles: 0 → 2 → 3.
    counter_params = [
        call[2].get("counter__gt") for call in client.json_calls if "job_events" in call[1]
    ]
    assert counter_params == ["0", "2"]


def test_stream_events_follows_pagination_within_one_cycle() -> None:
    """A single poll cycle that spans two AWX pages must return both."""
    client = _FakeClient(
        json_responses=[
            {"results": [{"counter": 1, "event": "x"}], "next": "/api/v2/.../?page=2"},
            {"results": [{"counter": 2, "event": "y"}], "next": None},
            _terminal_record(status="successful"),
        ]
    )
    monitor = PollingJobMonitor(cast(RawHttpResourceClient, client), sleep=lambda _: None)
    job = Job(id=7, kind="job", status="successful")  # already terminal
    events = list(monitor.stream_events(job))
    assert [ev.counter for ev in events] == [1, 2]


def test_stream_events_forwards_filter_params() -> None:
    client = _FakeClient(
        json_responses=[{"results": [], "next": None}],
    )
    monitor = PollingJobMonitor(cast(RawHttpResourceClient, client), sleep=lambda _: None)
    job = Job(id=7, kind="job", status="successful")
    list(monitor.stream_events(job, params={"event": "runner_on_failed", "host": "web-01"}))
    params = client.json_calls[0][2]
    assert params["event"] == "runner_on_failed"
    assert params["host"] == "web-01"
    assert params["counter__gt"] == "0"
