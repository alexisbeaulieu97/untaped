"""Why an ``awx test`` case did not pass: the responsible system, what kind of failure, evidence.

:class:`CaseFailure` is the core :class:`ErrorInfo` of the failed row (its
``category`` selects the exit code and whether a retry can help, and its
``hint`` says what to do), extended with the :class:`FailureEvidence` an agent
reads to fix the cause.

The rules are pure and the first match wins:

- ``awx.suite``: AWX refused the launch as asked (unknown name, a field the
  template does not prompt for), before any job ran.
- ``awx.credentials``: the API rejected the token or a permission, or a job
  (or the update it waited for) ended in ``error`` looking up a credential.
- ``awx.controller``: the API was unreachable or failed while the run polled
  or read; a job or its update ended in ``error``; a job was canceled outside
  the run, failed with a controller explanation (the reaper) or with events
  not yet readable; or it never left ``pending``/``waiting`` before the
  timeout.
- ``awx.scm`` / ``awx.inventory``: AWX names a failed project or inventory
  update in ``Previous Task Failed: {…}``, even when the case expected the
  job to fail (a ref not overridable, found before launching, is
  ``awx.scm`` too).
- ``awx.expectation``: the job ran its playbook as asked, but an expectation
  did not hold.
- ``awx.hosts``: the job failed and every failed task is an unreachable host.
- ``awx.playbook``: the job failed any other way, or a timeout while it ran.

A failure whose error is not AWX's (an untaped bug, local setup) keeps its
own system. The runner reads the job, the update, the failed tasks and the
responsible execution's log; this module decides.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from untaped.capabilities.awx.domain.job import HostSummary, Job, JobEvent
from untaped.capability_api import ErrorCategory, ErrorInfo, UntapedError, q

SUITE = "awx.suite"
CREDENTIALS = "awx.credentials"
CONTROLLER = "awx.controller"
SCM = "awx.scm"
INVENTORY = "awx.inventory"
HOSTS = "awx.hosts"
PLAYBOOK = "awx.playbook"
EXPECTATION = "awx.expectation"

FAILED_TASK_EVENTS = ("runner_on_failed", "runner_on_async_failed", "runner_on_unreachable")
"""Job events that end a task on a host in failure."""

_NOT_STARTED = frozenset({"new", "pending", "waiting"})
"""Statuses of a job the controller has not started running yet."""

_MAX_DETAIL = 1000
"""Characters of a failed task's ``msg`` / ``stderr`` kept in a result."""
_MAX_TRACEBACK = 2000
"""Characters of the end of a ``result_traceback`` kept as evidence."""

_UPDATES: dict[str, tuple[str, ErrorCategory]] = {
    "project_update": (SCM, ErrorCategory.FAILED),
    # A broken inventory source is the environment, not the change.
    "inventory_update": (INVENTORY, ErrorCategory.CONFIG),
}
_PREVIOUS_TASK_FAILED = "Previous Task Failed: "
_JOB_TYPE = re.compile(r'"job_type":\s*"([a-z_]+)"')
_JOB_NAME = re.compile(r'"job_name":\s*"(.*)",\s*"job_id"', re.DOTALL)
_JOB_ID = re.compile(r'"job_id":\s*"?(\d+)')
_JOB_TIMEOUT = "job terminated due to timeout"
"""AWX's explanation of a job that ran past its template's own timeout."""
_TRANSPORT = (
    "connection refused",
    "connection reset",
    "connection closed",
    "connection aborted",
    "timed out",
    "timeout",
    "operationalerror",
)
"""An error message naming the network or the database, not a credential."""
_SUITE_CATEGORIES = frozenset(
    {ErrorCategory.USAGE, ErrorCategory.INVALID, ErrorCategory.NOT_FOUND, ErrorCategory.CONFLICT}
)
_HINTS = {
    SUITE: "fix the suite, then run `untaped awx test validate`",
    CREDENTIALS: "fix the token (`untaped awx ping` checks it) or the job's credentials in AWX",
    CONTROLLER: "retry later; `untaped awx ping` checks the controller",
    SCM: "push the branch, or let the project override it (allow_override and "
    "ask_scm_branch_on_launch)",
    HOSTS: "retry later, once the hosts are reachable",
    PLAYBOOK: "fix the playbook: read failure.evidence (failed_tasks, log_tail)",
    EXPECTATION: "compare expectations with what the job did; fix the change or the case",
}


class FailedTask(BaseModel):
    """A task that failed on a host, from its job event."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    host: str | None
    task: str | None
    status: Literal["failed", "unreachable"]
    msg: str | None
    stderr: str | None
    """The end of the module's stderr (where errors usually are)."""

    @classmethod
    def from_event(cls, event: JobEvent) -> FailedTask:
        res = event.res or {}
        return cls(
            host=event.host_name,
            task=event.task,
            status="unreachable" if event.event == "runner_on_unreachable" else "failed",
            msg=clip(_text(res.get("msg")), _MAX_DETAIL),
            stderr=clip(_text(res.get("stderr")), _MAX_DETAIL, keep_end=True),
        )


class ChangedTask(BaseModel):
    """A task that changed a host, from its job event."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    host: str | None
    task: str | None


class RelatedExecution(BaseModel):
    """The update the job waited for, which failed first: the one responsible."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str
    id: int
    name: str | None
    status: str
    url: str | None


class FailureEvidence(BaseModel):
    """What shows the cause; each field is ``null`` when it does not apply or was not read."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    job_explanation: str | None = None
    """AWX's note on why the job ended."""
    result_traceback: str | None = None
    """The end of the controller's traceback of a job that ended in ``error``."""
    related: RelatedExecution | None = None
    """The failed update the job depended on."""
    log_tail: tuple[str, ...] | None = None
    """The last log lines of the responsible execution (``related`` when set, else the job)."""
    failed_tasks: tuple[FailedTask, ...] | None = None
    """The responsible execution's failed tasks (``ignore_errors`` and rescued ones left out)."""
    unreachable_hosts: tuple[str, ...] | None = None
    """Hosts among ``failed_tasks`` that could not be reached."""
    changed_tasks: tuple[ChangedTask, ...] | None = None
    """The tasks an ``idempotent`` case's rerun changed (the first 100)."""
    note: str | None = None
    """A secondary problem that did not decide the failure (a log that failed to download)."""

    @classmethod
    def of(
        cls,
        job: Job,
        *,
        related: RelatedExecution | None = None,
        log_tail: Sequence[str] | None = None,
        failed_tasks: Sequence[FailedTask] | None = None,
        note: str | None = None,
    ) -> FailureEvidence:
        unreachable = None
        if failed_tasks is not None:
            hosts = {t.host or "?" for t in failed_tasks if t.status == "unreachable"}
            unreachable = tuple(sorted(hosts))
        return cls(
            job_explanation=job.job_explanation or None,
            result_traceback=clip(job.result_traceback or None, _MAX_TRACEBACK, keep_end=True),
            related=related,
            log_tail=None if log_tail is None else tuple(log_tail),
            failed_tasks=None if failed_tasks is None else tuple(failed_tasks),
            unreachable_hosts=unreachable,
            note=note,
        )


class CaseFailure(ErrorInfo):
    """The ``failure`` of a case that did not pass: an :class:`ErrorInfo` plus evidence.

    ``system`` is one of the ``awx.*`` systems above, or the error's own
    system when AWX is not responsible.
    """

    evidence: FailureEvidence = Field(default_factory=FailureEvidence)


def failure(
    system: str, category: ErrorCategory, message: str, *, hint: str | None = None
) -> CaseFailure:
    """A :class:`CaseFailure`; ``hint`` defaults to the system's advice."""
    return CaseFailure(
        system=system,
        category=category,
        retryable=category.retryable,
        message=message,
        hint=hint or _HINTS.get(system),
    )


def failure_system(error: BaseException, *, launching: bool) -> str:
    """The system responsible for ``error`` while launching, or while polling or reading.

    An error that is not AWX's keeps its own system; a rejected token or
    permission is the credentials'; a refused launch is the suite's (the
    scm's for an ``scm_branch`` the template does not prompt for); anything
    else is the controller's.
    """
    info = ErrorInfo.from_exception(error)
    if not info.system.startswith("awx"):
        return info.system
    if info.category in (ErrorCategory.AUTH, ErrorCategory.PERMISSION):
        return CREDENTIALS
    if not launching or info.category not in _SUITE_CATEGORIES:
        return CONTROLLER
    if isinstance(error, UntapedError) and error.details.get("field") == "scm_branch":
        return SCM
    return SUITE


def request_failure(
    error: BaseException, *, launching: bool = False, message: str | None = None
) -> CaseFailure:
    """A launch, poll or read the run could not complete, keeping ``error``'s category and hint."""
    info = ErrorInfo.from_exception(error)
    system = failure_system(error, launching=launching)
    return failure(system, info.category, message or info.message, hint=info.hint)


def timeout_failure(job: Job, message: str) -> CaseFailure:
    """A case whose job was still unfinished at its timeout.

    A job that never started waited on the controller (capacity, a stuck
    dependency); one that was running hung in the playbook.
    """
    if job.status in _NOT_STARTED:
        return failure(CONTROLLER, ErrorCategory.UNAVAILABLE, message)
    return failure(PLAYBOOK, ErrorCategory.FAILED, message)


def responsible_update(job: Job) -> Job | None:
    """The project or inventory update AWX names in ``Previous Task Failed: {…}``.

    AWX does not escape the update's name, so a name with a quote or a
    backslash is read by pattern when the JSON does not parse. The returned
    update's status is ``unknown`` until it is read.
    """
    text = job.job_explanation or ""
    if _PREVIOUS_TASK_FAILED not in text:
        return None
    named = text.split(_PREVIOUS_TASK_FAILED, 1)[1]
    try:
        parsed = json.loads(named)
        kind, name, job_id = parsed["job_type"], parsed.get("job_name"), int(parsed["job_id"])
    except ValueError, TypeError, KeyError:
        kind_match, id_match = _JOB_TYPE.search(named), _JOB_ID.search(named)
        if kind_match is None or id_match is None:
            return None
        name_match = _JOB_NAME.search(named)
        kind, job_id = kind_match.group(1), int(id_match.group(1))
        name = name_match.group(1) if name_match else None
    if kind not in _UPDATES:
        return None
    return Job(id=job_id, kind=kind, name=name, status="unknown")


def unrescued(
    tasks: Sequence[FailedTask], hosts: Mapping[str, HostSummary] | None
) -> tuple[FailedTask, ...]:
    """``tasks`` without the failures a ``rescue`` block handled.

    Ansible reports a rescued task as failed, but the host's summary then
    counts no failure: a failed task on a host whose summary says
    ``failed: 0`` was rescued. Without summaries every task is kept.
    """
    if hosts is None:
        return tuple(tasks)
    return tuple(
        task
        for task in tasks
        if task.status != "failed" or task.host is None or _failed_on(task.host, hosts)
    )


def _failed_on(host: str, hosts: Mapping[str, HostSummary]) -> bool:
    """Whether ``host`` counts a failure (a host missing from the summaries does)."""
    summary = hosts.get(host)
    return summary is None or summary.failed > 0


def finished_failure(
    job: Job,
    *,
    update: Job | None,
    update_url: str | None,
    status_held: bool,
    reasons: Sequence[str],
    failed_tasks: Sequence[FailedTask] | None,
) -> CaseFailure | None:
    """The failure of a finished job, or ``None`` when the case passed.

    ``update`` is the :func:`responsible_update` as read from AWX (its real
    status), and ``failed_tasks`` are the responsible execution's (the
    update's when there is one, else the job's; ``None`` when unread).
    ``reasons`` are the expectations that did not hold.
    """
    if update is not None:
        return _update_failure(update, update_url, failed_tasks)
    if not reasons:
        return None
    if job.status in ("error", "canceled"):
        return _ended_failure(job, "job")
    if status_held or job.status == "successful":
        return failure(EXPECTATION, ErrorCategory.FAILED, "; ".join(reasons))
    return _failed_job(job, failed_tasks)


def _update_failure(
    update: Job, url: str | None, failed_tasks: Sequence[FailedTask] | None
) -> CaseFailure:
    related = RelatedExecution(
        kind=update.kind, id=update.id, name=update.name, status=update.status, url=url
    )
    what = f"{update.kind.replace('_', ' ')} {update.id}"
    if update.name:
        what += f" for {q(update.name)}"
    if update.status == "error":
        found = _ended_failure(update, what)
    else:
        system, category = _UPDATES[update.kind]
        log = f"read its log: `untaped awx jobs logs {update.id} --kind {update.kind}`"
        hint = f"{_HINTS[system]}; {log}" if system in _HINTS else log
        found = failure(system, category, f"{what} failed{_first_reason(failed_tasks)}", hint=hint)
    return found.model_copy(update={"evidence": FailureEvidence(related=related)})


def _ended_failure(execution: Job, what: str) -> CaseFailure:
    """An execution that ended in ``error`` or was canceled: the controller's, or a credential's."""
    if execution.status == "canceled":
        return failure(
            CONTROLLER, ErrorCategory.UNAVAILABLE, f"{what} was canceled outside this run"
        )
    last = _last_line(execution.result_traceback)
    why = execution.job_explanation or last
    said = f"{execution.job_explanation or ''}\n{last or ''}".lower()
    if "credential" in said and not any(marker in said for marker in _TRANSPORT):
        return failure(CREDENTIALS, ErrorCategory.AUTH, f"{what} could not use a credential: {why}")
    ended = f"{what} ended in error" + (f": {why}" if why else "")
    return failure(CONTROLLER, ErrorCategory.UNAVAILABLE, ended)


def tasks_unread(job: Job) -> CaseFailure:
    """A finished job whose ``failed_tasks`` expectation could not be checked."""
    message = f"failed_tasks not checked: {_events_unread(job)}"
    return failure(CONTROLLER, ErrorCategory.UNAVAILABLE, message)


def _events_unread(job: Job) -> str:
    """Why a finished job's failed tasks are unknown."""
    if job.event_processing_finished is False:
        return "AWX has not processed its events yet"
    return "its events could not be read"


def _failed_job(job: Job, failed_tasks: Sequence[FailedTask] | None) -> CaseFailure:
    if failed_tasks is None:
        message = f"job failed, but {_events_unread(job)}, so the failed task is unknown"
        return failure(CONTROLLER, ErrorCategory.UNAVAILABLE, message)
    tasks = list(failed_tasks)
    if tasks and all(task.status == "unreachable" for task in tasks):
        hosts = ", ".join(sorted({task.host or "?" for task in tasks}))
        message = f"unreachable: {hosts}{_first_reason(tasks)}"
        return failure(HOSTS, ErrorCategory.UNAVAILABLE, message)
    failed = [task for task in tasks if task.status == "failed"]
    if failed:
        first = failed[0]
        message = f"task {q(first.task or '?')} failed on {first.host or '?'}"
        message += _first_reason(failed)
        if len(failed) > 1:
            message += f" (and {len(failed) - 1} more)"
        return failure(PLAYBOOK, ErrorCategory.FAILED, message)
    explanation = job.job_explanation
    if explanation and _JOB_TIMEOUT not in explanation.lower():
        # The controller failed a job it lost track of (the reaper), not the playbook.
        return failure(CONTROLLER, ErrorCategory.UNAVAILABLE, f"job failed: {explanation}")
    if explanation:
        return failure(PLAYBOOK, ErrorCategory.FAILED, f"job failed: {explanation}")
    message = "job failed without a failed task (a syntax error or a missing role?): see log_tail"
    return failure(PLAYBOOK, ErrorCategory.FAILED, message)


def _first_reason(tasks: Sequence[FailedTask] | None) -> str:
    """``: <why>`` from the first failed task that says why, else nothing.

    Why is the first line of the module's ``msg``, else the last of its stderr.
    """
    for task in tasks or ():
        why = next(iter((task.msg or "").splitlines()), None) or _last_line(task.stderr)
        if why:
            return f": {why.strip()}"
    return ""


def _last_line(text: str | None) -> str | None:
    lines = [line for line in (text or "").splitlines() if line.strip()]
    return lines[-1].strip() if lines else None


def clip(text: str | None, limit: int, *, keep_end: bool = False) -> str | None:
    """``text`` cut to ``limit`` characters plus an ellipsis (its end with ``keep_end``)."""
    if text is None or len(text) <= limit:
        return text
    return "…" + text[-limit:] if keep_end else text[:limit] + "…"


def _text(value: Any) -> str | None:
    """A module result value as text (``None`` and empty stay ``None``)."""
    if value is None or value == "":
        return None
    return value if isinstance(value, str) else str(value)
