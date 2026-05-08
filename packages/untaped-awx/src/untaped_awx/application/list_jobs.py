"""List recent AWX execution records (jobs, workflow_jobs, …).

Encapsulates the "newest-first" default so every list-jobs caller —
``untaped awx jobs list`` today, ``--track`` worker code tomorrow — picks
up the same ordering without each one re-applying the convention.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from untaped_awx.application.ports import JobRecordRepository


class ListJobs:
    def __init__(self, repo: JobRecordRepository) -> None:
        self._repo = repo

    def __call__(
        self,
        *,
        kind: str = "job",
        params: dict[str, str] | None = None,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        merged = dict(params or {})
        merged.setdefault("order_by", "-id")
        return self._repo.list(kind=kind, params=merged, limit=limit)
