"""Poll kind-specific Controller status, stdout and event endpoints.

Ordinary jobs expose job_events; project/inventory updates and ad-hoc commands
expose events. Workflow jobs expose status only: they have neither events nor
stdout, so tracking uses stream_status without inventing child routes.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse

from untaped.api import paginate_pages
from untaped.capabilities.awx.domain import Job, JobEvent
from untaped.capabilities.awx.domain.job import JOB_ROUTES
from untaped.capabilities.awx.errors import AwxApiError

if TYPE_CHECKING:
    from untaped.capabilities.awx.application.ports import RawHttpResourceClient

SleepFn = Callable[[float], None]
_EVENT_PAGE_SIZE = 200
_EVENT_MAX_PAGES = 10_000


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

    def stream_status(self, job: Job) -> Iterator[Job]:
        """Emit initial status and changes until terminal using only the detail route."""
        current = job
        yield current
        while not current.is_terminal:
            self._sleep(self._interval)
            refreshed = self.fetch(current)
            if refreshed.status != current.status:
                yield refreshed
            current = refreshed

    def fetch_stdout(self, job: Job, *, start_line: int = 0) -> list[str]:
        api_path = _api_path_for(job)
        if not JOB_ROUTES[job.kind].stdout:
            raise AwxApiError(f"{job.kind} does not expose stdout; use jobs get/wait for status")
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
        events_path = JOB_ROUTES[job.kind].events
        if events_path is None:
            raise AwxApiError(f"{job.kind} does not expose events; use jobs get/wait for status")
        last = from_counter
        current = job
        while True:
            for record in _follow_pages(
                self._client,
                f"{api_path}/{current.id}/{events_path}/",
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
    routes = JOB_ROUTES.get(job.kind)
    if routes is None:
        raise AwxApiError(f"unsupported execution kind: {job.kind}")
    return routes.collection


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

    def fetch(cursor: str | None) -> tuple[list[dict[str, Any]], str | None]:
        page_num = cursor or "1"
        response = client.request(
            "GET",
            path,
            params={**params, "page": page_num, "page_size": str(_EVENT_PAGE_SIZE)},
        )
        return list(response.get("results") or []), _next_page_cursor(response.get("next"))

    yield from paginate_pages(fetch, limit=None, max_pages=_EVENT_MAX_PAGES)


def _next_page_cursor(next_url: object) -> str | None:
    if not isinstance(next_url, str) or not next_url:
        return None
    page_values = parse_qs(urlparse(next_url).query).get("page")
    if not page_values:
        return None
    return page_values[0]
