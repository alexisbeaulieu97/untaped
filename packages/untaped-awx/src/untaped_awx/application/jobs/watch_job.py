"""Use case: poll a Job until it reaches a terminal state."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable

from untaped_awx.application.jobs.ports import JobRecords
from untaped_awx.domain import Job
from untaped_awx.domain.jobs.job import poll_until_terminal

SleepFn = Callable[[float], None]


class WatchJob:
    def __init__(
        self,
        client: JobRecords,
        *,
        sleep: SleepFn = time.sleep,
        poll_interval: float = 2.0,
    ) -> None:
        self._client = client
        self._sleep = sleep
        self._interval = poll_interval

    def __call__(
        self,
        job: Job,
        *,
        timeout: float | None = None,
        on_state: Callable[[Job], None] | None = None,
    ) -> Job:
        """Return the terminal state, or the latest one once ``timeout`` passed.

        ``on_state`` sees each polled state that is not terminal yet; what it
        raises stops the watch.
        """

        def fetch(current: Job) -> Job:
            record = self._client.job(kind=current.kind, job_id=current.id)
            latest = Job.model_validate({**record, "kind": current.kind})
            if on_state is not None and not latest.is_terminal:
                on_state(latest)
            return latest

        states = poll_until_terminal(
            job, fetch, sleep=self._sleep, interval=self._interval, timeout=timeout
        )
        return deque(states, maxlen=1)[0]
