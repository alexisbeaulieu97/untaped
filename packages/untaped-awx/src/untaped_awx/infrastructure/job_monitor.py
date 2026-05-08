"""Concrete :class:`JobMonitor` that polls AWX for status, stdout, and events.

AWX v2's REST surface is request/response — no SSE, no websocket — so
"live tail" is implemented as a 2-second polling loop against three
endpoints, kept in lock-step with the job's terminal status:

- ``GET /<api_path>/<id>/`` — drives terminal detection (same as
  :class:`untaped_awx.application.WatchJob`).
- ``GET /<api_path>/<id>/stdout/?format=txt&start_line=N`` — text-only
  log tail. We track the last line index ourselves; AWX honours
  ``start_line`` as an *exclusive* offset.
- ``GET /<api_path>/<id>/job_events/?counter__gt=N&order_by=counter`` —
  paginated structured-event stream. ``counter`` is monotonic per job
  so we tail by ``counter__gt``.

Bare ``api_path`` strings (``jobs``, ``workflow_jobs``, …) come from
:data:`untaped_awx.domain.job.KIND_TO_API_PATH` so the same monitor
handles every execution kind without crossing layer boundaries — the
map is a domain fact about AWX execution records.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any

from untaped_awx.domain import Job, JobEvent
from untaped_awx.domain.job import KIND_TO_API_PATH

if TYPE_CHECKING:
    from untaped_awx.application.ports import RawHttpResourceClient

SleepFn = Callable[[float], None]


class PollingJobMonitor:
    """Polling-based :class:`JobMonitor` adapter."""

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

    def fetch(self, job: Job) -> Job:
        api_path = _api_path_for(job)
        record = self._client.request("GET", f"{api_path}/{job.id}/")
        return Job.model_validate({**record, "kind": job.kind})

    def fetch_stdout(self, job: Job, *, start_line: int = 0) -> list[str]:
        api_path = _api_path_for(job)
        text = self._client.request_text(
            "GET",
            f"{api_path}/{job.id}/stdout/",
            params={"format": "txt", "start_line": str(start_line)},
        )
        return text.splitlines()

    def stream_stdout(self, job: Job, *, start_line: int = 0) -> Iterator[str]:
        cursor = start_line
        current = job
        # Emit existing lines first, then poll until terminal, then drain a
        # final time so we never miss the tail emitted between the last
        # poll and the status transition.
        while True:
            lines = self.fetch_stdout(current, start_line=cursor)
            yield from lines
            cursor += len(lines)
            if current.is_terminal:
                return
            self._sleep(self._interval)
            current = self.fetch(current)

    def stream_events(
        self,
        job: Job,
        *,
        from_counter: int = 0,
        params: dict[str, str] | None = None,
        follow: bool = True,
    ) -> Iterator[JobEvent]:
        api_path = _api_path_for(job)
        last = from_counter
        current = job
        while True:
            for record in _follow_pages(
                self._client,
                f"{api_path}/{current.id}/job_events/",
                {**(params or {}), "counter__gt": str(last), "order_by": "counter"},
            ):
                ev = JobEvent.model_validate(record)
                if ev.counter > last:
                    last = ev.counter
                yield ev
            if not follow or current.is_terminal:
                return
            self._sleep(self._interval)
            current = self.fetch(current)


def _api_path_for(job: Job) -> str:
    return KIND_TO_API_PATH.get(job.kind, job.kind)


def _follow_pages(
    client: RawHttpResourceClient,
    path: str,
    params: dict[str, str],
) -> Iterator[dict[str, Any]]:
    """Follow AWX pagination across one ``job_events`` poll cycle.

    AWX caps ``page_size``; a busy job can produce thousands of events
    in a 2-second window. We walk ``next`` (i.e. bump ``page``) until
    the server says we're done so a single :meth:`stream_events` cycle
    doesn't lose events to pagination.
    """
    page_num = 1
    while True:
        response = client.request(
            "GET",
            path,
            params={**params, "page": str(page_num), "page_size": "200"},
        )
        yield from response.get("results") or []
        if not response.get("next"):
            return
        page_num += 1
