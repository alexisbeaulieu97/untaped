"""Tests for GetJob.

Single-record fetch through :class:`JobRecordRepository` — preserves the
raw AWX dict so ``--format yaml`` keeps every field.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from untaped_awx.application import GetJob


class _FakeJobRepo:
    def __init__(self, *, record: dict[str, Any] | None = None) -> None:
        self._record = record
        self.get_calls: list[dict[str, Any]] = []

    def list(
        self,
        *,
        kind: str,
        params: dict[str, str] | None = None,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        raise NotImplementedError

    def get(self, *, kind: str, job_id: int) -> dict[str, Any]:
        self.get_calls.append({"kind": kind, "job_id": job_id})
        if self._record is None:
            raise KeyError(job_id)
        return self._record


def test_get_job_defaults_to_kind_job() -> None:
    repo = _FakeJobRepo(record={"id": 7, "status": "successful"})
    GetJob(repo)(job_id=7)  # type: ignore[arg-type]
    assert repo.get_calls[0] == {"kind": "job", "job_id": 7}


def test_get_job_passes_kind_through() -> None:
    repo = _FakeJobRepo(record={"id": 7, "status": "successful"})
    GetJob(repo)(kind="workflow_job", job_id=42)  # type: ignore[arg-type]
    assert repo.get_calls[0] == {"kind": "workflow_job", "job_id": 42}


def test_get_job_returns_dict_verbatim() -> None:
    """The raw dict must round-trip — losing fields would break
    ``jobs get --format yaml`` callers who depend on full AWX shape."""
    raw = {
        "id": 7,
        "status": "successful",
        "stdout_url": "/api/v2/jobs/7/stdout/",
        "summary_fields": {"job_template": {"name": "deploy"}},
        "extra_vars": "{}",
    }
    repo = _FakeJobRepo(record=raw)
    out = GetJob(repo)(kind="job", job_id=7)  # type: ignore[arg-type]
    assert out == raw


def test_get_job_propagates_repo_errors() -> None:
    repo = _FakeJobRepo(record=None)
    with pytest.raises(KeyError):
        GetJob(repo)(kind="job", job_id=999)  # type: ignore[arg-type]
