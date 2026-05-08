"""Fetch a single AWX execution record."""

from __future__ import annotations

from typing import Any

from untaped_awx.application.ports import JobRecordRepository


class GetJob:
    def __init__(self, repo: JobRecordRepository) -> None:
        self._repo = repo

    def __call__(self, *, kind: str = "job", job_id: int) -> dict[str, Any]:
        return self._repo.get(kind=kind, job_id=job_id)
