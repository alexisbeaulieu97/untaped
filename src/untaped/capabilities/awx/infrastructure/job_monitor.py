"""Poll kind-specific Controller status, stdout and event endpoints.

Ordinary jobs expose job_events; project/inventory updates and ad-hoc commands
expose events. Workflow jobs expose status only: they have neither events nor
stdout, so following one uses stream_status without inventing child routes.

Events are the one incremental reader of a log: following a log
(:meth:`PollingJobMonitor.stream_stdout`) asks each poll only for the events
after the last one read (``counter__gt``) and prints their stdout without ANSI
colours, and a log's tail (:meth:`PollingJobMonitor.tail_stdout`) reads only
the newest events (``order_by=-counter`` with a small ``page_size``). Only
:meth:`PollingJobMonitor.fetch_stdout` downloads a whole log, once.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse

from untaped.capabilities.awx.domain import Job, JobEvent
from untaped.capabilities.awx.domain.job import JOB_ROUTES, poll_until_terminal
from untaped.capabilities.awx.errors import AwxApiError
from untaped.capability_api import paginate_pages

if TYPE_CHECKING:
    from untaped.capabilities.awx.application.ports import RawHttpResourceClient

SleepFn = Callable[[float], None]
_EVENT_PAGE_SIZE = 200
_EVENT_MAX_PAGES = 10_000
_SETTLE_POLLS = 5
"""Extra polls a finished job's log gets while AWX still saves its events."""
_GAP_READS = 5
"""Reads a log follow waits for an event missing below later ones before it skips it."""


class PollingJobMonitor:
    """Polling-based :class:`JobMonitor` adapter."""

    def __init__(
        self,
        client: RawHttpResourceClient,
        *,
        sleep: SleepFn = time.sleep,
        poll_interval: float = 2.0,
        timeout: float | None = None,
        warn: Callable[[str], None] | None = None,
    ) -> None:
        self._client = client
        self._warn = warn
        self._sleep = sleep
        self._interval = poll_interval
        self._timeout = timeout
        """Seconds each follow loop polls before giving up; ``None`` waits until terminal."""

    def fetch(self, job: Job) -> Job:
        api_path = _api_path_for(job)
        record = self._client.request("GET", f"{api_path}/{job.id}/")
        return Job.model_validate({**record, "kind": job.kind})

    def _poll(self, job: Job) -> Iterator[Job]:
        return poll_until_terminal(
            job, self.fetch, sleep=self._sleep, interval=self._interval, timeout=self._timeout
        )

    def stream_status(self, job: Job) -> Iterator[Job]:
        """Emit initial status and changes until terminal using only the detail route."""
        previous: str | None = None
        for current in self._poll(job):
            if current.status != previous:
                yield current
            previous = current.status

    def fetch_stdout(self, job: Job) -> list[str]:
        api_path = _api_path_for(job)
        if not JOB_ROUTES[job.kind].stdout:
            raise AwxApiError(f"{job.kind} does not expose stdout; use jobs get/wait for status")
        # ``txt`` answers large logs with a "too large to display" stub, and
        # neither honours ``start_line``; ``txt_download`` has no size cap.
        text = self._client.request_text(
            "GET",
            f"{api_path}/{job.id}/stdout/",
            params={"format": "txt_download"},
        )
        return text.splitlines()

    def stream_stdout(self, job: Job, *, from_counter: int = 0) -> Iterator[str]:
        """Yield the log lines of events after ``from_counter`` until the job is terminal."""
        cursor = _Cursor(from_counter)
        current = job
        # Each poll reads only the events after the cursor; the terminal
        # state reads a final time so we never miss the tail emitted
        # between the last poll and the status transition.
        for current in self._poll(job):
            yield from self._new_lines(current, cursor)
        # Saved events can trail the terminal status: keep reading (briefly)
        # until they are all in, PLAY RECAP included.
        for _ in range(_SETTLE_POLLS):
            if not (current.is_terminal and current.event_processing_finished is False):
                return
            self._sleep(self._interval)
            current = self.fetch(current)
            yield from self._new_lines(current, cursor)
        if current.is_terminal and current.event_processing_finished is False and self._warn:
            self._warn(
                f"{current.kind} {current.id}: AWX is still saving its events; the log "
                f"(and its PLAY RECAP) may be cut short; see `jobs logs {current.id} --kind "
                f"{current.kind}` later"
            )

    def tail_stdout(self, job: Job, lines: int) -> tuple[list[str], int]:
        """The log's last ``lines`` lines and the newest event's counter (``0``: none yet).

        Reads the newest events first (``order_by=-counter``), ``lines`` per
        page, and older pages (``counter__lt``) only until it has enough lines:
        usually one request, however long the log.
        """
        path = _events_path(job)
        page_size = min(max(lines, 1), _EVENT_PAGE_SIZE)
        newest_first: list[JobEvent] = []
        found = 0
        query = {"order_by": "-counter", "page_size": str(page_size)}
        while True:
            response = self._client.request("GET", path, params=query)
            page = [JobEvent.model_validate(record) for record in response.get("results") or []]
            newest_first.extend(page)
            found += sum(len(event.lines) for event in page)
            if found >= lines or len(page) < page_size:
                break
            query = {**query, "counter__lt": str(page[-1].counter)}
        log = [line for event in reversed(newest_first) for line in event.lines]
        newest = newest_first[0].counter if newest_first else 0
        return (log[-lines:] if lines > 0 else []), newest

    def stream_events(
        self,
        job: Job,
        *,
        from_counter: int = 0,
        params: dict[str, str] | None = None,
        follow: bool = True,
    ) -> Iterator[JobEvent]:
        last = from_counter
        for current in self._poll(job):
            for ev in self._events_after(current, last, params):
                if ev.counter > last:
                    last = ev.counter
                yield ev
            if not follow:
                return

    def _events_after(
        self, job: Job, counter: int, params: dict[str, str] | None = None
    ) -> Iterator[JobEvent]:
        """One read of every event after ``counter``, in counter order, across pages."""
        query = {**(params or {}), "counter__gt": str(counter), "order_by": "counter"}
        for record in _follow_pages(self._client, _events_path(job), query):
            yield JobEvent.model_validate(record)

    def _new_lines(self, job: Job, cursor: _Cursor) -> Iterator[str]:
        for event in self._events_after(job, cursor.done):
            if cursor.take(event.counter):
                yield from event.lines
        cursor.read_done()


class _Cursor:
    """The events a log follow has printed: every counter up to ``done``, plus a few past a gap.

    AWX can save a later event before an earlier one, so the next read
    starts after the last *contiguous* counter and skips what it printed.
    A gap still open after :data:`_GAP_READS` reads is given up on, so an
    event that never arrives cannot make every later read start before it.
    """

    def __init__(self, start: int) -> None:
        self.done = start
        self._ahead: set[int] = set()
        self._stuck = 0
        self._last_done = start

    def take(self, counter: int) -> bool:
        """Record ``counter``; ``False`` when it was already printed."""
        if counter <= self.done or counter in self._ahead:
            return False
        self._ahead.add(counter)
        self._advance()
        return True

    def read_done(self) -> None:
        """Count a read that left the same gap open; skip the gap after too many."""
        stuck = self._ahead and self.done == self._last_done
        self._stuck = self._stuck + 1 if stuck else 0
        self._last_done = self.done
        if self._stuck >= _GAP_READS:
            self.done = min(self._ahead)
            self._ahead.remove(self.done)
            self._advance()
            self._stuck = 0

    def _advance(self) -> None:
        while self.done + 1 in self._ahead:
            self.done += 1
            self._ahead.remove(self.done)


def _events_path(job: Job) -> str:
    api_path = _api_path_for(job)
    events_path = JOB_ROUTES[job.kind].events
    if events_path is None:
        raise AwxApiError(f"{job.kind} does not expose events; use jobs get/wait for status")
    return f"{api_path}/{job.id}/{events_path}/"


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
