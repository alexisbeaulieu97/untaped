"""Job terminal states and the AWX event shapes the models must accept."""

from __future__ import annotations

import pytest

from untaped.capabilities.awx.domain import Job, JobEvent


@pytest.mark.parametrize(
    ("status", "terminal"),
    [
        ("new", False),
        ("pending", False),
        ("waiting", False),
        ("running", False),
        ("successful", True),
        ("failed", True),
        ("error", True),
        ("canceled", True),
    ],
)
def test_is_terminal(status: str, terminal: bool) -> None:
    assert Job(id=1, kind="job", status=status).is_terminal is terminal


def test_job_ignores_unknown_fields() -> None:
    job = Job.model_validate({"id": 1, "kind": "job", "status": "running", "elapsed": 12.3})
    assert job.status == "running"


def test_job_event_accepts_int_host_with_separate_host_name() -> None:
    """Regression: AWX returns ``host`` as an FK id and ``host_name`` as the
    denormalised string; ``host: str | None`` rejected every host event."""
    ev = JobEvent.model_validate(
        {"counter": 5, "event": "runner_on_ok", "host": 7, "host_name": "web-01"}
    )
    assert (ev.host, ev.host_name) == (7, "web-01")
