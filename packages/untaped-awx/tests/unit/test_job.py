from __future__ import annotations

import pytest
from untaped_awx.domain import Job, JobEvent


@pytest.mark.parametrize("status", ["new", "pending", "waiting", "running"])
def test_non_terminal_status(status: str) -> None:
    job = Job(id=1, kind="job", status=status)
    assert not job.is_terminal


@pytest.mark.parametrize("status", ["successful", "failed", "error", "canceled"])
def test_terminal_status(status: str) -> None:
    job = Job(id=1, kind="job", status=status)
    assert job.is_terminal


def test_job_ignores_unknown_fields() -> None:
    job = Job.model_validate({"id": 1, "kind": "job", "status": "running", "elapsed": 12.3})
    assert job.status == "running"


def test_job_event_accepts_int_host_with_separate_host_name() -> None:
    """AWX returns ``host`` as an FK id (int) and ``host_name`` as the
    denormalised string. Both must validate without error — the original
    ``host: str | None`` typing tripped a ValidationError on every event
    that referenced a real host."""
    raw = {
        "counter": 5,
        "event": "runner_on_ok",
        "host": 7,
        "host_name": "web-01",
        "task": "install",
    }
    ev = JobEvent.model_validate(raw)
    assert ev.host == 7
    assert ev.host_name == "web-01"


def test_job_event_accepts_null_host() -> None:
    """``playbook_on_*`` rows have no host — both fields are absent / null."""
    ev = JobEvent.model_validate({"counter": 1, "event": "playbook_on_play_start"})
    assert ev.host is None
    assert ev.host_name is None
