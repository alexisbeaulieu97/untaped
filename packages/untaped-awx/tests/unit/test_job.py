from __future__ import annotations

import pytest
from untaped_awx.domain import Job


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
