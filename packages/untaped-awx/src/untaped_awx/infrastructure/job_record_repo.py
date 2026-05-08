"""Concrete :class:`JobRecordRepository` implementation.

Wraps a :class:`RawHttpResourceClient` and translates ``Job.kind`` into
the matching AWX collection path via :data:`KIND_TO_API_PATH`. The
lookup keeps a ``<kind>`` fallback so callers passing an unknown kind
hit the same path the prior CLI helper used (defensive, rarely fires).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from untaped_awx.domain.job import KIND_TO_API_PATH

if TYPE_CHECKING:
    from untaped_awx.application.ports import RawHttpResourceClient


class JobRecordRepository:
    def __init__(self, client: RawHttpResourceClient) -> None:
        self._client = client

    def list(
        self,
        *,
        kind: str,
        params: dict[str, str] | None = None,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        return self._client.paginate_path(
            f"{KIND_TO_API_PATH.get(kind, kind)}/",
            params=params,
            limit=limit,
        )

    def get(self, *, kind: str, job_id: int) -> dict[str, Any]:
        return self._client.request("GET", f"{KIND_TO_API_PATH.get(kind, kind)}/{job_id}/")
