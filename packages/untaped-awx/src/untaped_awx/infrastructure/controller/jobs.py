"""Where controller API v2 serves executions: records, logs, events, host summaries.

Lists are newest first unless the caller passes its own ``order_by``; a
job's host summaries (its PLAY RECAP per host) are read failed hosts first.
A workflow job's nodes are read as they ran, and its pending approvals
approved or denied.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from untaped_awx.errors import AwxApiError
from untaped_awx.infrastructure.controller._part import ControllerPart

if TYPE_CHECKING:
    from untaped_awx.domain import Job


@dataclass(frozen=True)
class JobPaths:
    """Where an execution kind lives: its collection, its events, its web UI page."""

    collection: str
    events: str | None
    """The events listing under a record; ``None``: the kind has none."""
    ui_type: str
    """The web UI's name for the kind in ``jobs/<ui_type>/<id>/output``."""


JOB_PATHS: dict[str, JobPaths] = {
    "job": JobPaths("jobs", "job_events", "playbook"),
    "workflow_job": JobPaths("workflow_jobs", None, "workflow"),
    "project_update": JobPaths("project_updates", "events", "project"),
    "inventory_update": JobPaths("inventory_updates", "events", "inventory"),
    "ad_hoc_command": JobPaths("ad_hoc_commands", "events", "command"),
}
"""Each execution kind's paths, by the ``kind`` a :class:`Job` carries."""


def _paths(kind: str) -> JobPaths:
    paths = JOB_PATHS.get(kind)
    if paths is None:
        raise AwxApiError(f"unsupported execution kind: {kind}")
    return paths


def _collection(kind: str) -> str:
    return _paths(kind).collection


def _events(job: Job) -> str:
    paths = _paths(job.kind)
    if paths.events is None:
        raise AwxApiError(f"no events path for {job.kind}")
    return f"{paths.collection}/{job.id}/{paths.events}/"


class Jobs(ControllerPart):
    """:class:`~untaped_awx.application.jobs.ports.JobRecords` and ``JobOutput``."""

    def jobs(
        self,
        *,
        kind: str,
        params: dict[str, str] | None = None,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        return self._paginate(
            f"{_collection(kind)}/",
            params={"order_by": "-id", **(params or {})},
            limit=limit,
            collection=True,
        )

    def job(self, *, kind: str, job_id: int) -> dict[str, Any]:
        return self._request("GET", f"{_collection(kind)}/{job_id}/")

    def job_host_summaries(
        self, job: Job, params: Mapping[str, str] | None = None
    ) -> Iterator[dict[str, Any]]:
        return self._paginate(
            f"{_collection(job.kind)}/{job.id}/job_host_summaries/",
            params={"order_by": "-failed,host_name", **(params or {})},
        )

    def workflow_job_nodes(self, job: Job) -> Iterator[dict[str, Any]]:
        return self._paginate(f"{_collection(job.kind)}/{job.id}/workflow_nodes/")

    def decide_approval(self, approval_id: int, *, approve: bool) -> None:
        action = "approve" if approve else "deny"
        self._request("POST", f"workflow_approvals/{approval_id}/{action}/")

    def cancel_job(self, *, kind: str, job_id: int) -> None:
        self._request("POST", f"{_collection(kind)}/{job_id}/cancel/")

    def relaunch_job(self, *, kind: str, job_id: int, hosts: str | None = None) -> dict[str, Any]:
        return self._request(
            "POST",
            f"{_collection(kind)}/{job_id}/relaunch/",
            json={"hosts": hosts} if hosts else {},
        )

    def job_stdout(self, job: Job) -> str:
        # ``txt`` answers large logs with a "too large to display" stub, and
        # neither honours ``start_line``; ``txt_download`` has no size cap.
        return self._text(
            f"{_collection(job.kind)}/{job.id}/stdout/", params={"format": "txt_download"}
        )

    def job_events(self, job: Job, params: Mapping[str, str]) -> Iterator[dict[str, Any]]:
        return self._paginate(_events(job), params=params)

    def job_events_page(self, job: Job, params: Mapping[str, str]) -> list[dict[str, Any]]:
        return list(self._request("GET", _events(job), params=params).get("results") or [])


__all__ = ["JOB_PATHS", "JobPaths", "Jobs"]
