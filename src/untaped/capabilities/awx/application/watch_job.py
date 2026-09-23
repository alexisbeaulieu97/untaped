"""Use case: poll a Job until it reaches a terminal state.

Doesn't require a :class:`ResourceSpec` — execution records aren't in
the catalog. We hit the right ``<api_path>`` directly via the client's
``request`` escape hatch.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable

from untaped.capabilities.awx.application.ports import RawHttpResourceClient
from untaped.capabilities.awx.domain import Job
from untaped.capabilities.awx.domain.job import KIND_TO_API_PATH, poll_until_terminal

SleepFn = Callable[[float], None]


class WatchJob:
    def __init__(
        self,
        client: RawHttpResourceClient,
        *,
        sleep: SleepFn = time.sleep,
        poll_interval: float = 2.0,
    ) -> None:
        self._client = client
        self._sleep = sleep
        self._interval = poll_interval

    def __call__(self, job: Job, *, timeout: float | None = None) -> Job:
        """Return the terminal state, or the latest one once ``timeout`` passed."""
        api_path = KIND_TO_API_PATH.get(job.kind, job.kind)

        def fetch(current: Job) -> Job:
            record = self._client.request("GET", f"{api_path}/{current.id}/")
            return Job.model_validate({**record, "kind": current.kind})

        states = poll_until_terminal(
            job, fetch, sleep=self._sleep, interval=self._interval, timeout=timeout
        )
        return deque(states, maxlen=1)[0]
