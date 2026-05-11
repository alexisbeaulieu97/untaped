"""Tests for ListJobs.

The use case wraps a :class:`JobRecordRepository`, applies the
"newest-first" default (``order_by=-id``), and forwards everything else
verbatim. Stubbed via an inline fake — no respx, no FakeAap.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast

from untaped_awx.application import ListJobs
from untaped_awx.application.ports import JobRecordRepository


class _FakeJobRepo:
    def __init__(self, *, records: list[dict[str, Any]] | None = None) -> None:
        self._records = records or []
        self.list_calls: list[dict[str, Any]] = []

    def list(
        self,
        *,
        kind: str,
        params: dict[str, str] | None = None,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        self.list_calls.append({"kind": kind, "params": dict(params or {}), "limit": limit})
        return iter(self._records)

    def get(self, *, kind: str, job_id: int) -> dict[str, Any]:
        raise NotImplementedError


def test_list_jobs_defaults_to_kind_job() -> None:
    repo = _FakeJobRepo()
    list(ListJobs(cast(JobRecordRepository, repo))())
    assert repo.list_calls[0]["kind"] == "job"


def test_list_jobs_applies_newest_first_default() -> None:
    """Without an explicit ``order_by``, the use case asks for ``-id``."""
    repo = _FakeJobRepo()
    list(ListJobs(cast(JobRecordRepository, repo))(kind="workflow_job"))
    assert repo.list_calls[0]["params"] == {"order_by": "-id"}


def test_list_jobs_preserves_caller_order_by() -> None:
    """Caller-supplied ``order_by`` wins over the default."""
    repo = _FakeJobRepo()
    list(
        ListJobs(cast(JobRecordRepository, repo))(
            params={"order_by": "started", "status": "running"}
        )
    )
    assert repo.list_calls[0]["params"] == {"order_by": "started", "status": "running"}


def test_list_jobs_forwards_kind_and_limit() -> None:
    repo = _FakeJobRepo(records=[{"id": 1}, {"id": 2}])
    out = list(
        ListJobs(cast(JobRecordRepository, repo))(
            kind="project_update",
            params={"status": "successful"},
            limit=50,
        )
    )
    assert [r["id"] for r in out] == [1, 2]
    call = repo.list_calls[0]
    assert call["kind"] == "project_update"
    assert call["limit"] == 50
    # Default order_by added on top of the caller's filters.
    assert call["params"] == {"status": "successful", "order_by": "-id"}


def test_list_jobs_handles_none_params() -> None:
    """Passing ``params=None`` must not blow up; the use case still
    applies the newest-first default."""
    repo = _FakeJobRepo()
    list(ListJobs(cast(JobRecordRepository, repo))(params=None))
    assert repo.list_calls[0]["params"] == {"order_by": "-id"}
