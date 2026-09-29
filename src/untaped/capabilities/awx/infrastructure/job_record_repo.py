"""Concrete :class:`JobRecordRepository` implementation.

Wraps a :class:`RawHttpResourceClient` and translates ``Job.kind`` into
the matching AWX collection path via :data:`KIND_TO_API_PATH`. Lists are
newest-first unless the caller passes its own ``order_by``; a job's host
summaries (its PLAY RECAP per host) are read failed hosts first. A workflow
job's nodes are read as they ran, and its pending approvals approved or
denied. The
lookup keeps a ``<kind>`` fallback so callers passing an unknown kind
hit the same path the prior CLI helper used (defensive, rarely fires).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING, Any

from untaped.capabilities.awx.domain.job import KIND_TO_API_PATH

if TYPE_CHECKING:
    from untaped.capabilities.awx.application.ports import RawHttpResourceClient
    from untaped.capabilities.awx.domain import Job


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
            params={"order_by": "-id", **(params or {})},
            limit=limit,
        )

    def get(self, *, kind: str, job_id: int) -> dict[str, Any]:
        return self._client.request("GET", f"{KIND_TO_API_PATH.get(kind, kind)}/{job_id}/")

    def host_summaries(
        self, job: Job, params: Mapping[str, str] | None = None
    ) -> Iterator[dict[str, Any]]:
        """``jobs/<id>/job_host_summaries/``: one paginated listing, read lazily.

        Failed and unreachable hosts (AWX's ``failed``) come first, so a
        reader that stops early keeps them. ``params`` filter the listing.
        """
        return self._client.paginate_path(
            f"{KIND_TO_API_PATH[job.kind]}/{job.id}/job_host_summaries/",
            params={"order_by": "-failed,host_name", **(params or {})},
        )

    def workflow_nodes(self, job: Job) -> Iterator[dict[str, Any]]:
        """``workflow_jobs/<id>/workflow_nodes/``: the nodes of a workflow job as they ran."""
        return self._client.paginate_path(f"{KIND_TO_API_PATH[job.kind]}/{job.id}/workflow_nodes/")

    def decide_approval(self, approval_id: int, *, approve: bool) -> None:
        """``POST workflow_approvals/<id>/approve/`` (or ``deny/``) a pending approval."""
        action = "approve" if approve else "deny"
        self._client.request("POST", f"workflow_approvals/{approval_id}/{action}/")

    def cancel(self, *, kind: str, job_id: int) -> None:
        """``POST <collection>/<id>/cancel/``: AWX answers 202 and stops the job later."""
        self._client.request("POST", f"{KIND_TO_API_PATH.get(kind, kind)}/{job_id}/cancel/")

    def relaunch(self, *, kind: str, job_id: int, hosts: str | None = None) -> dict[str, Any]:
        """``POST <collection>/<id>/relaunch/``; returns the new execution record."""
        return self._client.request(
            "POST",
            f"{KIND_TO_API_PATH.get(kind, kind)}/{job_id}/relaunch/",
            json={"hosts": hosts} if hosts else {},
        )
