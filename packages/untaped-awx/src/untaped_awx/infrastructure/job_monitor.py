"""Poll kind-specific Controller status, stdout and event endpoints.

Ordinary jobs expose job_events; project/inventory updates and ad-hoc commands
expose events. Workflow jobs expose status only: they have neither events nor
stdout, so following one uses stream_status without inventing child routes.

Events are the one incremental reader of a log: following a log
(:meth:`PollingJobMonitor.stream_stdout`) asks each poll only for the events
after the last contiguous one (``counter__gt``, untruncated), holds an event
AWX saved before an earlier one until the gap fills, and prints their stdout
without ANSI colours; a log's tail (:meth:`PollingJobMonitor.tail_stdout`)
reads only the newest events (``order_by=-counter``, a small first page).
Only :meth:`PollingJobMonitor.fetch_stdout` downloads a whole log, once.
:meth:`PollingJobMonitor.settled` waits (briefly) until a finished job's
events are saved.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse

from untaped.sdk import paginate_pages, plural
from untaped_awx.domain import Job, JobEvent
from untaped_awx.domain.job import JOB_ROUTES, poll_until_terminal
from untaped_awx.errors import AwxApiError

if TYPE_CHECKING:
    from untaped_awx.application.ports import RawHttpResourceClient

SleepFn = Callable[[float], None]
_EVENT_PAGE_SIZE = 200
_EVENT_MAX_PAGES = 10_000
_SETTLE_POLLS = 5
"""Extra polls a finished job's log gets while AWX still saves its events."""
_TAIL_MAX_PAGES = 10
"""Pages of events a tail reads at most, however few lines they print."""
_FULL_TEXT = {"no_truncate": "1"}
"""AWX cuts event ``stdout`` short unless asked not to."""


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

    def settled(self, job: Job) -> Job:
        """``job`` re-read (a few times at most) until AWX has saved all its events.

        A finished job's events can trail its status; readers of its failed
        tasks or host summaries wait here first. Anything but a finished job
        whose ``event_processing_finished`` is ``False`` is returned as is.
        """
        current = job
        for _ in range(_SETTLE_POLLS):
            if not _unsaved(current):
                break
            self._sleep(self._interval)
            current = self.fetch(current)
        return current

    def stream_stdout(self, job: Job, *, from_counter: int = 0) -> Iterator[str]:
        """Yield the log lines of events after ``from_counter`` until the job is terminal."""
        for event in self._follow(job, from_counter, _FULL_TEXT):
            yield from event.lines

    def tail_stdout(self, job: Job, lines: int) -> tuple[list[str], int]:
        """The log's last ``lines`` lines and the newest event's counter (``0``: none yet).

        Reads the newest events first (``order_by=-counter``): a first page of
        twice as many events as lines, then older pages (``counter__lt``) only
        until it has enough lines, at most :data:`_TAIL_MAX_PAGES` pages.
        Usually one request, however long the log.
        """
        path = _events_path(job)
        first = min(max(2 * lines, 1), _EVENT_PAGE_SIZE)
        pages = 0

        def fetch(below: str | None) -> tuple[list[dict[str, Any]], str | None]:
            nonlocal pages
            pages += 1
            size = first if below is None else _EVENT_PAGE_SIZE
            query = {"order_by": "-counter", "page_size": str(size), **_FULL_TEXT}
            if below is not None:
                query["counter__lt"] = below
            results = list(self._client.request("GET", path, params=query).get("results") or [])
            more = len(results) >= size and pages < _TAIL_MAX_PAGES
            return results, str(results[-1]["counter"]) if more else None

        newest_first: list[JobEvent] = []
        found = 0
        for record in paginate_pages(fetch, limit=None, max_pages=_TAIL_MAX_PAGES):
            if found >= lines and newest_first:
                break
            event = JobEvent.model_validate(record)
            newest_first.append(event)
            found += len(event.lines)
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
        """Yield events after ``from_counter`` in counter order.

        An unfiltered follow holds an event AWX saved before an earlier one
        until the earlier one arrives, as a log follow does. A filtered follow
        cannot tell a missing event from a filtered one, so it reads after the
        newest event it has seen and accepts losing one AWX saves late.
        """
        if follow and not params:
            yield from self._follow(job, from_counter)
            return
        last = from_counter
        for current in self._poll(job):
            for ev in self._events_after(current, last, params):
                if ev.counter > last:
                    last = ev.counter
                yield ev
            if not follow:
                return

    def _follow(
        self, job: Job, from_counter: int, params: dict[str, str] | None = None
    ) -> Iterator[JobEvent]:
        """Every event after ``from_counter``, in counter order, until the job is done.

        Each poll reads only the events after the last contiguous one; a
        later event waits for the ones before it. Once the job is terminal
        and its events are saved (or the settle polls run out), events still
        waiting are yielded anyway and the missing ones are warned about.
        """
        cursor = _Cursor(from_counter)
        current = job
        # The terminal state reads a final time so we never miss the tail
        # emitted between the last poll and the status transition.
        for current in self._poll(job):
            yield from self._read_into(current, cursor, params)
        # Saved events can trail the terminal status: keep reading (briefly)
        # until they are all in, PLAY RECAP included.
        for _ in range(_SETTLE_POLLS):
            if not _unsaved(current):
                break
            self._sleep(self._interval)
            current = self.fetch(current)
            yield from self._read_into(current, cursor, params)
        if _unsaved(current) and self._warn:
            self._warn(
                f"{current.kind} {current.id}: AWX is still saving its events; the log "
                f"(and its PLAY RECAP) may be cut short; see `jobs logs {current.id} --kind "
                f"{current.kind}` later"
            )
        held, missing = cursor.release()
        yield from held
        if missing and self._warn:
            self._warn(
                f"{current.kind} {current.id}: {plural(missing, 'event')} never arrived; "
                "its lines are missing from the log"
            )

    def _events_after(
        self, job: Job, counter: int, params: dict[str, str] | None = None
    ) -> Iterator[JobEvent]:
        """One read of every event after ``counter``, in counter order, across pages."""
        query = {**(params or {}), "counter__gt": str(counter), "order_by": "counter"}
        for record in _follow_pages(self._client, _events_path(job), query):
            yield JobEvent.model_validate(record)

    def _read_into(
        self, job: Job, cursor: _Cursor, params: dict[str, str] | None
    ) -> Iterator[JobEvent]:
        for event in self._events_after(job, cursor.done, params):
            yield from cursor.take(event)


class _Cursor:
    """Where a follow stands: every counter up to ``done`` yielded, later ones held.

    AWX can save a later event before an earlier one, so the next read
    starts after the last *contiguous* counter and a later event waits for
    the gap below it to fill.
    """

    def __init__(self, start: int) -> None:
        self.done = start
        self._held: dict[int, JobEvent] = {}

    def take(self, event: JobEvent) -> list[JobEvent]:
        """The events ``event`` lets through, in counter order (none while a gap is open)."""
        if event.counter <= self.done or event.counter in self._held:
            return []
        self._held[event.counter] = event
        released: list[JobEvent] = []
        while self.done + 1 in self._held:
            self.done += 1
            released.append(self._held.pop(self.done))
        return released

    def release(self) -> tuple[list[JobEvent], int]:
        """Every held event in counter order, and how many counters below them never came."""
        held = [self._held.pop(counter) for counter in sorted(self._held)]
        if not held:
            return [], 0
        missing = held[-1].counter - self.done - len(held)
        self.done = held[-1].counter
        return held, missing


def _unsaved(job: Job) -> bool:
    """A finished job whose events AWX is still saving."""
    return job.is_terminal and job.event_processing_finished is False


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
