"""What a finished job carries, and how it is read back for a suite case.

Its host summaries (cut at 500 hosts, completed with filtered reads past the
cut when a check needs them), its failed tasks nothing handled (from its
events), the failed update it names, the newest log lines and the tasks a
rerun changed. Its own tasks, log tail, hosts and update are kept on its
:class:`~untaped_awx.application.suites.workflow.JobRead` once read. A read that fails
never raises: it gives ``None``, the update as the job named it, or the
failure that says why. The checks
(:mod:`untaped_awx.application.suites.checks`) and the runner read through
these functions; this module calls neither.
"""

from __future__ import annotations

from itertools import islice
from typing import Any

from untaped_awx.application.suites.collaborators import SuiteReading
from untaped_awx.application.suites.workflow import (
    JobRead,
)
from untaped_awx.domain import Job
from untaped_awx.domain.jobs.job import (
    HostSummary,
    by_host,
    host_summaries,
)
from untaped_awx.domain.suites.case_failure import (
    FAILED_TASK_EVENTS,
    UPDATE_KINDS,
    CaseFailure,
    ChangedTask,
    FailedTask,
    FailureEvidence,
    request_failure,
    responsible_update,
    unhandled,
)
from untaped_awx.domain.suites.expectations import (
    ExecutionChecks,
    ExpectationResult,
)
from untaped_awx.domain.suites.workflow_run import (
    WORKFLOW_JOB,
)

LOG_TAIL_LINES = 40
"""Log lines a case that did not pass carries in its result."""
CHANGED_TASKS_LIMIT = 100
"""Changed tasks a rerun that was not idempotent lists in its evidence."""
_CHANGED_TASKS = {"event": "runner_on_ok", "changed": "true"}
"""The events of tasks that changed a host (a loop's items fold into their task's)."""


def _gather(reading: SuiteReading, job: Job, failure: CaseFailure, read: JobRead) -> CaseFailure:
    """``failure`` with its evidence, from the responsible execution: ``related`` or the job.

    A workflow has no log nor events of its own: a node's failure already
    carries its node job's evidence, and any other keeps the workflow's
    explanation.
    """
    if job.kind == WORKFLOW_JOB:
        if failure.evidence.node is not None:
            return failure
        evidence = FailureEvidence.of(job, note=failure.evidence.note)
        return failure.model_copy(update={"evidence": evidence})
    source = read.source if failure.evidence.related is not None else job
    if read.tasks_read:
        tasks = read.tasks
    else:
        tasks = () if source.status == "successful" else _failed_tasks(reading, source, None)
    log = read.log if source is job else None
    tail = _tail(reading, source, read) if log is None else tuple(log[-LOG_TAIL_LINES:])
    evidence = FailureEvidence.of(
        job,
        related=failure.evidence.related,
        log_tail=tail,
        failed_tasks=tasks,
        note=failure.evidence.note,
    )
    return failure.model_copy(update={"evidence": evidence})


def _failed_tasks(
    reading: SuiteReading, job: Job, hosts: dict[str, HostSummary] | None
) -> tuple[FailedTask, ...] | None:
    """The job's failed tasks nothing handled (see :func:`unhandled`); ``None`` if unread.

    ``None`` too when there are none while AWX is still saving the events.
    """
    params = {"event__in": ",".join(FAILED_TASK_EVENTS)}
    try:
        events = list(reading.event_reader(job, params=params, follow=False))
    except Exception:
        return None
    tasks = unhandled(events, hosts)
    if not tasks and job.event_processing_finished is False:
        return None  # the failures may not be saved yet
    return tasks


def _tail(reading: SuiteReading, job: Job, read: JobRead) -> tuple[str, ...] | None:
    """The last log lines, from the newest events only, read once; ``None`` if unreadable."""
    key = (job.kind, job.id)
    if key not in read.tails:
        try:
            read.tails[key] = tuple(reading.tail_reader(job, LOG_TAIL_LINES))
        except Exception:
            read.tails[key] = None
    return read.tails[key]


def _changed_tasks(
    reading: SuiteReading, job: Job, read: JobRead | None
) -> tuple[ChangedTask, ...] | None:
    """The first tasks that changed a host, from one filtered events read (``None``: unread).

    A workflow's are those of its playbook jobs.
    """
    try:
        jobs = [job]
        if read is not None and read.workflow is not None:
            jobs = list(read.workflow.playbook_jobs(job))
        changed = (
            ChangedTask(host=event.host_name, task=event.task)
            for each in jobs
            for event in reading.event_reader(each, params=dict(_CHANGED_TASKS), follow=False)
            if event.changed
        )
        return tuple(islice(changed, CHANGED_TASKS_LIMIT))
    except Exception:
        return None


def _host_checks(
    reading: SuiteReading,
    job: Job,
    expect: ExecutionChecks,
    fields: dict[str, Any],
    read: JobRead,
) -> list[ExpectationResult] | CaseFailure:
    """The host checks, or why the host summaries they need could not be read."""
    _ensure_hosts(reading, job, fields, read)
    if read.all_hosts is not None:
        return expect.check_hosts(read.all_hosts)
    hosts, hosts_error = fields["hosts"], read.hosts_error
    if hosts is not None and fields["hosts_truncated"]:
        hosts, hosts_error = _beyond_the_cut(reading, job, hosts, expect)
    if hosts is None and hosts_error is not None:
        return request_failure(hosts_error, message=f"host summaries unreadable: {hosts_error}")
    return expect.check_hosts(hosts or {})


def _job_tasks(
    reading: SuiteReading, job: Job, fields: dict[str, Any], read: JobRead
) -> tuple[FailedTask, ...] | None:
    """The job's own failed tasks, read once; a successful job has none to read."""
    if job.status == "successful":
        read.tasks = ()
    else:
        # The job's summaries tell rescued failures from real ones.
        _ensure_hosts(reading, job, fields, read)
        hosts = fields["hosts"] if read.all_hosts is None else read.all_hosts
        read.tasks = _failed_tasks(reading, job, hosts)
    read.tasks_read = True
    return read.tasks


def _beyond_the_cut(
    reading: SuiteReading, job: Job, kept: dict[str, HostSummary], expect: ExecutionChecks
) -> tuple[dict[str, HostSummary] | None, Exception | None]:
    """``kept`` plus every host a check needs that the 500-host cut left out.

    Reads the hosts over each bound (and the named ones) with filtered
    host summary reads, so no host past the cut can hide a failed check.
    """
    found = dict(kept)
    try:
        for params in expect.host_filters(known=kept):
            found |= by_host(reading.host_reader(job, params))
    except Exception as exc:
        return None, exc
    return found, None


def _ensure_hosts(reading: SuiteReading, job: Job, fields: dict[str, Any], read: JobRead) -> None:
    """Put the job's host summaries in the row once (``hosts``, ``hosts_truncated``).

    A workflow's job (or node job) has every record read, so its checks
    see every host; a job case's are read lazily, cut at 500 hosts.
    """
    if "hosts" in fields:
        return
    try:
        if read.workflow is not None:
            records = read.workflow.host_records(job)
            read.all_hosts = by_host(records)
            hosts, truncated = host_summaries(records)
        else:
            hosts, truncated = host_summaries(reading.host_reader(job))
    except Exception as exc:
        hosts, truncated, read.hosts_error = None, False, exc
    fields["hosts"], fields["hosts_truncated"] = hosts, truncated


def _update(reading: SuiteReading, job: Job, read: JobRead) -> Job | None:
    """The failed update the job names, as AWX has it now (``unknown`` if unreadable).

    Read once; ``None`` for a job that succeeded. A workflow node that ran
    an update itself is that update.
    """
    if not read.update_read:
        read.update, read.update_read = _read_update(reading, job), True
    return read.update


def _read_update(reading: SuiteReading, job: Job) -> Job | None:
    if job.status == "successful":
        return None
    if job.kind in UPDATE_KINDS:
        return job
    update = responsible_update(job)
    if update is None:
        return None
    try:
        return reading.job_reader.settled(reading.job_reader.fetch(update))
    except Exception:
        return update
