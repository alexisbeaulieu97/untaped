"""``untaped awx jobs``: inspect executions; the writes are in :mod:`.job_actions`."""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import TYPE_CHECKING, Annotated

from pydantic import Field

from untaped.sdk import (
    Failed,
    Group,
    Limit,
    Output,
    Short,
    UntapedError,
    UsageError,
    key,
)
from untaped_awx.application import TailJobLogs, WatchJob
from untaped_awx.commands._awx import AwxCommand
from untaped_awx.commands._job_targets import JOB_KIND_HELP, JobKind, JobsCommand, as_job_id
from untaped_awx.commands._options import Filters
from untaped_awx.commands.context import AwxContext
from untaped_awx.commands.event_render import render_event_text
from untaped_awx.commands.job_actions import Cancel, Relaunch
from untaped_awx.domain import Job, JobEvent
from untaped_awx.domain.jobs.job import JOB_ROUTES
from untaped_awx.records import JobEventRow, JobLogLine, JobRecord

if TYPE_CHECKING:
    from rich.text import Text

# Default ``table`` columns; a string is a key AWX sends that JobRecord does not declare.
# json, yaml and pipe keep whole records.
_JOB_LIST_COLUMNS = (
    key(JobRecord).id,
    key(JobRecord).name,
    key(JobRecord).status,
    "launch_type",
    "started",
    key(JobRecord).elapsed,
)
_JOB_GET_COLUMNS = (
    key(JobRecord).id,
    key(JobRecord).name,
    key(JobRecord).status,
    "started",
    "finished",
    key(JobRecord).elapsed,
    "job_explanation",
)
_JOB_WAIT_COLUMNS = (
    key(JobRecord).id,
    key(JobRecord).name,
    key(JobRecord).status,
    "finished_at",
    key(JobRecord).elapsed,
)


class List(AwxCommand):
    """List recent AWX jobs (newest first).

    --template takes digits as an id; match a numeric name with `--filter
    job_template__name=123`. Tables show a summary; json and yaml carry the
    whole record.
    """

    status: str | None = Field(
        None, description="Filter by AWX status (e.g. running, successful, failed)."
    )
    filter_: Filters
    limit: Limit = Limit(default=20)
    kind: JobKind = Field("job", description=JOB_KIND_HELP)
    template: str | None = Field(
        None,
        description=(
            "Only runs of this template (project or inventory source for updates): a name, "
            "or digits for an AWX id."
        ),
    )
    output: Output[JobRecord] = Output(columns=_JOB_LIST_COLUMNS)

    def run(self) -> Iterator[JobRecord]:
        filters = dict(self.filter_)
        if self.status:
            filters["status"] = self.status
        if self.template is not None:
            field = JOB_ROUTES[self.kind].template_field
            if field is None:
                raise UsageError(f"--template does not apply to {self.kind} executions")
            filters[field if self.template.isdigit() else f"{field}__name"] = self.template
        self.output.empty("No jobs found. Try a different --status or --filter.")
        with self.awx() as awx, awx.diag.progress("Loading jobs"):
            records = list(awx.jobs.list(kind=self.kind, params=filters, limit=self.limit.value))
        yield from (JobRecord.model_validate(record) for record in records)


class Get(JobsCommand):
    """Fetch one or more jobs by id."""

    output: Output[JobRecord] = Output(columns=_JOB_GET_COLUMNS)

    def run(self) -> Iterator[JobRecord]:
        rows: list[JobRecord] = []
        with self.awx() as awx:
            ids, kinds = self.targets()
            for ident in ids:
                try:
                    record = awx.jobs.get(kind=kinds.get(ident, self.kind), job_id=as_job_id(ident))
                except UntapedError as exc:
                    awx.diag.failure(exc, item=ident)
                    continue
                rows.append(JobRecord.model_validate(record))
        yield from rows


def _job(awx: AwxContext, ident: str, kind: str) -> tuple[int, Job]:
    """The execution ``ident`` names, read as the ``kind`` it is."""
    job_id = as_job_id(ident)
    record = awx.jobs.get(kind=kind, job_id=job_id)
    return job_id, Job.model_validate({**record, "kind": kind})


def _event_lines(row: JobEventRow | list[JobEventRow]) -> list[Text]:
    """``--format table``: each event as a line like the AWX UI's Output tab, coloured on a TTY."""
    return [
        render_event_text(JobEvent.model_validate(each.model_dump(exclude={"job"})))
        for each in ([row] if isinstance(row, JobEventRow) else row)
    ]


class Events(JobsCommand):
    """Stream the structured per-task event log for one or more jobs.

    Without `--follow` this drains each job's event log and exits; with
    `--follow` it polls AWX every 2s until each job is terminal. Filters reach
    AWX directly so you can scope server-side, e.g. `--filter
    event=runner_on_failed --filter host=web-01`. A workflow job has no
    events (use `jobs wait`).

    A table shows each event as a line, like the AWX UI's Output tab;
    `--columns` gives a table of the events' fields instead. Multiple ids
    drain serially with a `[<id>]` stderr breadcrumb between jobs; each row
    names its `job`.
    """

    follow: bool = Field(False, description="Tail events live until terminal.")
    from_counter: int = Field(0, description="Skip events with counter <= this value.")
    filter_: Filters = Field(
        description="Server-side AWX filter, KEY=VALUE (repeatable). E.g. event=runner_on_failed."
    )
    output: Output[JobEventRow] = Output(stream=True, render={"table": _event_lines})

    def run(self) -> Iterator[JobEventRow]:
        filters = dict(self.filter_)
        with self.awx() as awx:
            ids, kinds = self.targets()
            for ident in ids:
                try:
                    job_id, job = _job(awx, ident, kinds.get(ident, self.kind))
                    if len(ids) > 1:
                        awx.diag.info(f"[{job_id}]", unmuted=True)
                    for event in awx.monitor.stream_events(
                        job, from_counter=self.from_counter, params=filters, follow=self.follow
                    ):
                        yield JobEventRow(job=job_id, **event.model_dump())
                except UntapedError as exc:
                    awx.diag.failure(exc, item=ident)


class Logs(JobsCommand):
    """Print the stdout of one or more jobs. Supports follow / tail / grep.

    It downloads the full stdout. With --follow it reads only new events on
    each poll (colours removed); `--tail N --follow` starts from the newest
    events. Project and inventory updates have logs (--kind); a workflow job
    has none (use `jobs wait`).

    Multiple ids drain serially with a `[<id>]` stderr breadcrumb between
    jobs; each row names its `job`.
    """

    follow: bool = Field(False, description="Tail until terminal.")
    tail: int | None = Field(
        None, ge=0, description="Show only the last N historical lines before following."
    )
    grep: str | None = Field(
        None, description="Client-side regex; only matching lines are printed."
    )
    ignore_case: Annotated[bool, Short("-i")] = Field(False, description="Case-insensitive --grep.")
    output: Output[JobLogLine] = Output(stream=True, format="raw")

    def run(self) -> Iterator[JobLogLine]:
        if self.grep is not None:
            try:
                re.compile(self.grep, re.IGNORECASE if self.ignore_case else 0)
            except re.error as exc:
                raise UsageError(f"--grep {self.grep!r} is not a valid regex: {exc}") from exc
        with self.awx() as awx:
            ids, kinds = self.targets()
            for ident in ids:
                try:
                    job_id, job = _job(awx, ident, kinds.get(ident, self.kind))
                    if len(ids) > 1:
                        awx.diag.info(f"[{job_id}]", unmuted=True)
                    lines = TailJobLogs(awx.monitor)(
                        job,
                        follow=self.follow,
                        grep=self.grep,
                        ignore_case=self.ignore_case,
                        tail=self.tail,
                    )
                    for line in lines:
                        yield JobLogLine(job=job_id, line=line)
                except UntapedError as exc:
                    awx.diag.failure(exc, item=ident)


class Wait(JobsCommand):
    """Block until each named job reaches a terminal state.

    Exits non-zero on unsuccessful terminal states, timeout, or resolution
    failure, as `awx test` does. Multiple ids drain serially; the `--timeout`
    budget applies per id.
    """

    timeout: float | None = Field(
        None, ge=0, description="Seconds to wait before giving up (applies per id)."
    )
    output: Output[JobRecord] = Output(columns=_JOB_WAIT_COLUMNS)

    def run(self) -> Iterator[JobRecord]:
        rows: list[JobRecord] = []
        timed_out: list[int] = []
        with self.awx() as awx:
            ids, kinds = self.targets()
            for ident in ids:
                try:
                    job_id, job = _job(awx, ident, kinds.get(ident, self.kind))
                    final = WatchJob(awx.repo, sleep=awx.pause)(job, timeout=self.timeout)
                except UntapedError as exc:
                    awx.diag.failure(exc, item=ident)
                    continue
                if not final.is_terminal:
                    timed_out.append(job_id)
                rows.append(JobRecord.model_validate(final.model_dump(mode="json")))
        yield from rows
        for job_id in timed_out:
            self.ctx.diag.warning(f"job {job_id}: timed out before reaching a terminal state")
        if timed_out or any(row.status != "successful" for row in rows):
            raise Failed


class JobsGroup(Group):
    """Inspect, cancel and relaunch AWX jobs."""

    commands = (List, Get, Events, Logs, Wait, Cancel, Relaunch)


__all__ = ["Events", "Get", "JobsGroup", "List", "Logs", "Wait"]
