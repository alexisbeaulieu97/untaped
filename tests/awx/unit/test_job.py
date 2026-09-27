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


def test_job_event_keeps_the_module_result_out_of_rendered_rows() -> None:
    ev = JobEvent.model_validate(
        {
            "counter": 9,
            "event": "runner_on_failed",
            "event_data": {"res": {"msg": "boom", "rc": 2}, "ignore_errors": False},
        }
    )
    assert ev.res == {"msg": "boom", "rc": 2}
    assert "res" not in ev.model_dump()
    assert JobEvent.model_validate({"counter": 1, "event_data": {}}).res is None


def test_job_carries_the_ref_and_commit_it_ran() -> None:
    job = Job.model_validate(
        {"id": 1, "kind": "job", "status": "running", "scm_branch": "fix", "scm_revision": "abc"}
    )
    assert (job.scm_branch, job.scm_revision) == ("fix", "abc")
