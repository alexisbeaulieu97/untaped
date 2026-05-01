"""Use case: poll a Job until it reaches a terminal state.

Doesn't require a :class:`ResourceSpec` — execution records aren't in
the catalog. We hit the right ``<api_path>`` directly via the client's
``request`` escape hatch.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from untaped_awx.application.ports import ResourceClient
from untaped_awx.domain import Job

_KIND_TO_API_PATH: dict[str, str] = {
    "job": "jobs",
    "workflow_job": "workflow_jobs",
    "project_update": "project_updates",
    "inventory_update": "inventory_updates",
    "ad_hoc_command": "ad_hoc_commands",
}

SleepFn = Callable[[float], None]


class WatchJob:
    def __init__(
        self,
        client: ResourceClient,
        *,
        sleep: SleepFn = time.sleep,
        poll_interval: float = 2.0,
    ) -> None:
        self._client = client
        self._sleep = sleep
        self._interval = poll_interval

    def __call__(self, job: Job, *, timeout: float | None = None) -> Job:
        api_path = _KIND_TO_API_PATH.get(job.kind, job.kind)
        deadline = time.monotonic() + timeout if timeout is not None else None
        current = job
        while not current.is_terminal:
            if deadline is not None and time.monotonic() >= deadline:
                return current
            self._sleep(self._interval)
            record = self._client.request("GET", f"{api_path}/{current.id}/")
            current = Job.model_validate({**record, "kind": current.kind})
        return current
