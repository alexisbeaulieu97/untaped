"""The single job polling loop behind waiting and streaming."""

from __future__ import annotations

from untaped.capabilities.awx.domain import Job
from untaped.capabilities.awx.domain.job import poll_until_terminal


def _job(status: str) -> Job:
    return Job(id=1, kind="job", status=status)


def test_yields_initial_state_then_each_fetch_until_terminal() -> None:
    queued = [_job("running"), _job("successful")]
    sleeps: list[float] = []
    states = list(
        poll_until_terminal(
            _job("pending"), lambda _: queued.pop(0), sleep=sleeps.append, interval=2
        )
    )
    assert [state.status for state in states] == ["pending", "running", "successful"]
    assert sleeps == [2, 2]


def test_terminal_input_is_yielded_without_polling() -> None:
    def fetch(_job: Job) -> Job:
        raise AssertionError("must not poll a terminal job")

    states = list(poll_until_terminal(_job("failed"), fetch, sleep=lambda _: None, interval=1))
    assert [state.status for state in states] == ["failed"]


def test_timeout_stops_at_latest_state() -> None:
    states = list(
        poll_until_terminal(
            _job("running"),
            lambda job: job,
            sleep=lambda _: None,
            interval=0,
            timeout=0,
        )
    )
    assert [state.status for state in states] == ["running"]
