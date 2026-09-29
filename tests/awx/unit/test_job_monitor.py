"""Unit tests for :class:`PollingJobMonitor`.

Stubs the :class:`ResourceClient` so we can exercise the polling /
pagination / terminal-detection logic without a real httpx round trip.
The fake client is queue-driven: each test scripts what the next
``request`` / ``request_text`` call should return, and the monitor
walks the script until done.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from untaped.capabilities.awx.application.ports import RawHttpResourceClient
from untaped.capabilities.awx.domain import Job
from untaped.capabilities.awx.errors import AwxApiError
from untaped.capabilities.awx.infrastructure.job_monitor import PollingJobMonitor


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


def test_fetch_stdout_downloads_the_full_log() -> None:
    """``txt_download`` has no size cap (``txt`` returns a "too large" stub)."""
    client = _FakeClient(text_responses=["line-1\nline-2\n"])
    monitor = PollingJobMonitor(cast(RawHttpResourceClient, client), sleep=lambda _: None)
    lines = monitor.fetch_stdout(_running())
    assert lines == ["line-1", "line-2"]
    method, path, params = client.text_calls[0]
    assert method == "GET"
    assert path == "jobs/7/stdout/"
    assert params == {"format": "txt_download"}


def _events(*counters: int, stdout: str = "line {}") -> dict[str, Any]:
    """One page of events, each printing ``stdout`` with its counter."""
    return {
        "results": [{"counter": c, "stdout": stdout.format(c)} for c in counters],
        "next": None,
    }


def _event_params(client: _FakeClient) -> list[dict[str, str]]:
    return [params for _, path, params in client.json_calls if path.endswith("job_events/")]


def test_stream_stdout_reads_only_the_new_events_each_poll() -> None:
    """Following a log never re-downloads it: each poll asks for events after the last."""
    client = _FakeClient(
        json_responses=[
            _events(1, 2, stdout="\x1b[0;32mok {}\x1b[0m\r\nmore"),
            _terminal_record(status="successful"),
            _events(3),
        ]
    )
    sleeps: list[float] = []
    monitor = PollingJobMonitor(cast(RawHttpResourceClient, client), sleep=sleeps.append)
    lines = list(monitor.stream_stdout(_running()))
    assert lines == ["ok 1", "more", "ok 2", "more", "line 3"]
    assert [params["counter__gt"] for params in _event_params(client)] == ["0", "2"]
    assert {params["order_by"] for params in _event_params(client)} == {"counter"}
    assert client.text_calls == []
    assert sleeps == [2.0]


def test_stream_stdout_rereads_past_an_event_awx_saved_late() -> None:
    """AWX can save event 4 before event 3: the cursor stays below the gap until it fills."""
    client = _FakeClient(
        json_responses=[
            _events(1, 2, 4),
            _terminal_record(status="successful"),
            _events(3, 4, 5),
        ]
    )
    monitor = PollingJobMonitor(cast(RawHttpResourceClient, client), sleep=lambda _: None)
    lines = list(monitor.stream_stdout(_running()))
    assert lines == ["line 1", "line 2", "line 4", "line 3", "line 5"]
    assert [params["counter__gt"] for params in _event_params(client)] == ["0", "2"]


def test_stream_stdout_gives_up_on_an_event_that_never_arrives() -> None:
    """A gap still open after five more reads is skipped, so reads stop restarting below it."""
    running = {"id": 7, "status": "running"}
    client = _FakeClient(
        json_responses=[
            _events(1, 2, 4),
            *[response for _ in range(5) for response in (running, _events(4))],
            _terminal_record(status="successful"),
            _events(5),
        ]
    )
    monitor = PollingJobMonitor(cast(RawHttpResourceClient, client), sleep=lambda _: None)
    assert list(monitor.stream_stdout(_running())) == ["line 1", "line 2", "line 4", "line 5"]
    assert [params["counter__gt"] for params in _event_params(client)] == [
        "0",
        *["2"] * 5,
        "4",
    ]


def test_stream_stdout_starts_after_a_counter() -> None:
    client = _FakeClient(json_responses=[_events(8)])
    monitor = PollingJobMonitor(cast(RawHttpResourceClient, client), sleep=lambda _: None)
    job = Job(id=7, kind="job", status="successful")
    assert list(monitor.stream_stdout(job, from_counter=7)) == ["line 8"]
    assert _event_params(client)[0]["counter__gt"] == "7"


def test_stream_stdout_waits_for_a_finished_jobs_events_to_be_saved() -> None:
    """A finished job keeps streaming its tail (the PLAY RECAP) until
    ``event_processing_finished``."""
    client = _FakeClient(
        json_responses=[
            _events(1, stdout="a"),
            {"id": 7, "status": "successful", "event_processing_finished": True},
            _events(2, stdout="PLAY RECAP"),
        ],
    )
    monitor = PollingJobMonitor(cast(RawHttpResourceClient, client), sleep=lambda _: None)
    finished = Job(id=7, kind="job", status="successful", event_processing_finished=False)
    assert list(monitor.stream_stdout(finished)) == ["a", "PLAY RECAP"]
    assert len(_event_params(client)) == 2


def test_stream_stdout_settling_is_bounded() -> None:
    unsaved = {"id": 7, "status": "successful", "event_processing_finished": False}
    client = _FakeClient(json_responses=[_events(), unsaved] * 20)
    monitor = PollingJobMonitor(cast(RawHttpResourceClient, client), sleep=lambda _: None)
    finished = Job(id=7, kind="job", status="successful", event_processing_finished=False)
    assert list(monitor.stream_stdout(finished)) == []
    assert len(client.json_calls) < 20


def test_stream_stdout_warns_when_events_are_still_being_saved() -> None:
    unsaved = {"id": 7, "status": "failed", "event_processing_finished": False}
    client = _FakeClient(json_responses=[_events(), unsaved] * 20)
    warnings: list[str] = []
    monitor = PollingJobMonitor(
        cast(RawHttpResourceClient, client), sleep=lambda _: None, warn=warnings.append
    )
    finished = Job(id=7, kind="job", status="failed", event_processing_finished=False)
    list(monitor.stream_stdout(finished))
    assert warnings == [
        "job 7: AWX is still saving its events; the log (and its PLAY RECAP) may be cut "
        "short; see `jobs logs 7 --kind job` later"
    ]


def test_tail_stdout_reads_only_the_newest_events() -> None:
    """One request, newest first, sized to the lines wanted; returned oldest first."""
    client = _FakeClient(json_responses=[_events(9, 8, 7)])
    monitor = PollingJobMonitor(cast(RawHttpResourceClient, client), sleep=lambda _: None)
    lines, newest = monitor.tail_stdout(_running(), 3)
    assert (lines, newest) == (["line 7", "line 8", "line 9"], 9)
    assert _event_params(client) == [{"order_by": "-counter", "page_size": "3"}]


def test_tail_stdout_reads_older_events_until_it_has_enough_lines() -> None:
    client = _FakeClient(
        json_responses=[
            {"results": [{"counter": 9, "stdout": ""}, {"counter": 8, "stdout": "h"}]},
            {"results": [{"counter": 7, "stdout": "f\ng"}, {"counter": 6, "stdout": "e"}]},
        ]
    )
    monitor = PollingJobMonitor(cast(RawHttpResourceClient, client), sleep=lambda _: None)
    lines, newest = monitor.tail_stdout(_running(), 2)
    assert (lines, newest) == (["g", "h"], 9)
    assert _event_params(client) == [
        {"order_by": "-counter", "page_size": "2"},
        {"order_by": "-counter", "page_size": "2", "counter__lt": "8"},
    ]


@pytest.mark.parametrize(("wanted", "expected"), [(0, []), (5, ["line 1", "line 2"])])
def test_tail_stdout_of_a_short_log(wanted: int, expected: list[str]) -> None:
    client = _FakeClient(json_responses=[_events(2, 1)])
    monitor = PollingJobMonitor(cast(RawHttpResourceClient, client), sleep=lambda _: None)
    assert monitor.tail_stdout(_running(), wanted) == (expected, 2)


def test_an_empty_log_has_no_tail() -> None:
    monitor = PollingJobMonitor(cast(RawHttpResourceClient, _FakeClient()), sleep=lambda _: None)
    assert monitor.tail_stdout(_running(), 40) == ([], 0)


def test_a_workflow_job_has_no_log_to_follow() -> None:
    monitor = PollingJobMonitor(cast(RawHttpResourceClient, _FakeClient()), sleep=lambda _: None)
    workflow = Job(id=7, kind="workflow_job", status="running")
    with pytest.raises(AwxApiError, match="does not expose events"):
        monitor.tail_stdout(workflow, 40)
    with pytest.raises(AwxApiError, match="does not expose events"):
        list(monitor.stream_stdout(workflow))


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


def test_status_stream_emits_only_changes_and_stops_at_terminal() -> None:
    client = _FakeClient(
        json_responses=[
            _terminal_record(status="pending"),
            _terminal_record(status="running"),
            _terminal_record(status="running"),
            _terminal_record(status="successful"),
        ]
    )
    sleeps: list[float] = []
    monitor = PollingJobMonitor(cast(RawHttpResourceClient, client), sleep=sleeps.append)
    states = list(monitor.stream_status(Job(id=7, kind="workflow_job", status="pending")))
    assert [state.status for state in states] == ["pending", "running", "successful"]
    assert sleeps == [2.0] * 4
    assert [path for _, path, _ in client.json_calls] == ["workflow_jobs/7/"] * 4
    assert client.text_calls == []


def test_ad_hoc_events_use_supported_events_subpath() -> None:
    client = _FakeClient(
        json_responses=[{"results": [{"counter": 1, "event": "runner_on_ok"}], "next": None}]
    )
    monitor = PollingJobMonitor(cast(RawHttpResourceClient, client))
    events = list(monitor.stream_events(Job(id=7, kind="ad_hoc_command", status="successful")))
    assert [event.counter for event in events] == [1]
    assert client.json_calls[0][1] == "ad_hoc_commands/7/events/"
