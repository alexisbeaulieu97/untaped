"""Why an ``awx test`` case did not pass: the responsible system, what kind of failure, evidence.

:class:`CaseFailure` is the core :class:`ErrorInfo` of the failed row (its
``category`` selects the exit code and whether a retry can help, and its
``hint`` says what to do), extended with the :class:`FailureEvidence` an agent
reads to fix the cause. Its message is written ``summary``.

The rules are pure and the first match wins:

- ``awx.suite``: AWX refused the launch as asked (unknown name, a field the
  template does not prompt for), before any job ran.
- ``awx.credentials``: the API rejected the token or a permission, or a job
  ended in ``error`` looking up a credential.
- ``awx.controller``: the API was unreachable or failed while the run polled
  or read, a job ended in ``error`` (or was canceled) with no failed
  dependency, or a timeout while the job never left ``pending``/``waiting``.
- ``awx.scm`` / ``awx.inventory``: AWX names a failed project or inventory
  update in ``Previous Task Failed: {…}`` (a ref not pushed or not
  overridable, found before launching, is ``awx.scm`` too).
- ``awx.hosts``: the job failed and every failed task is an unreachable host.
- ``awx.playbook``: the job failed any other way, or a timeout while it ran.
- ``awx.expectation``: the job ran as intended, but an expectation did not hold.

The runner reads the job, its failed tasks and the responsible execution's
log; this module decides.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from untaped.capabilities.awx.domain.job import Job, JobEvent
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

NOT_STARTED = frozenset({"new", "pending", "waiting"})
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
_CREDENTIAL_LOOKUP = re.compile(r"credential", re.IGNORECASE)
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


class RelatedExecution(BaseModel):
    """The execution that failed before the job: the one responsible."""

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
    """The responsible execution's failed tasks (``ignore_errors`` ones left out)."""
    unreachable_hosts: tuple[str, ...] | None = None
    """Hosts among ``failed_tasks`` that could not be reached."""

    @classmethod
    def of(
        cls,
        job: Job,
        *,
        related: RelatedExecution | None = None,
        log_tail: Sequence[str] | None = None,
        failed_tasks: Sequence[FailedTask] | None = None,
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
        )


class CaseFailure(ErrorInfo):
    """The ``failure`` of a case that did not pass: an :class:`ErrorInfo` plus evidence.

    ``system`` is one of the ``awx.*`` systems above; the message is
    written (and read) as ``summary``.
    """

    model_config = ConfigDict(serialize_by_alias=True, validate_by_name=True)

    message: str = Field(alias="summary")
    evidence: FailureEvidence = Field(default_factory=FailureEvidence)


def failure(
    system: str,
    category: ErrorCategory,
    summary: str,
    *,
    hint: str | None = None,
    evidence: FailureEvidence | None = None,
) -> CaseFailure:
    """A :class:`CaseFailure`; ``hint`` defaults to the system's advice."""
    return CaseFailure(
        system=system,
        category=category,
        retryable=category.retryable,
        message=summary,
        hint=hint or _HINTS.get(system),
        evidence=evidence or FailureEvidence(),
    )


def launch_system(error: BaseException) -> str:
    """The system responsible for a launch AWX refused (before or at the POST)."""
    category = error.category if isinstance(error, UntapedError) else ErrorCategory.FAILED
    if category in (ErrorCategory.AUTH, ErrorCategory.PERMISSION):
        return CREDENTIALS
    if isinstance(error, UntapedError) and error.details.get("field") == "scm_branch":
        return SCM
    return SUITE if category in _SUITE_CATEGORIES else CONTROLLER


def request_failure(
    error: BaseException, *, launching: bool = False, summary: str | None = None
) -> CaseFailure:
    """A launch, poll or read the run could not complete, keeping ``error``'s category and hint.

    A refused launch is the suite's (or the credentials'); a failed poll or
    read is the controller's (or the credentials').
    """
    info = ErrorInfo.from_exception(error)
    system = launch_system(error) if launching else CONTROLLER
    if info.category in (ErrorCategory.AUTH, ErrorCategory.PERMISSION):
        system = CREDENTIALS
    return failure(system, info.category, summary or info.message, hint=info.hint)


def timeout_failure(job: Job, summary: str) -> CaseFailure:
    """A case whose job was still unfinished at its timeout.

    A job that never started waited on the controller (capacity, a stuck
    dependency); one that was running hung in the playbook.
    """
    if job.status in NOT_STARTED:
        return failure(CONTROLLER, ErrorCategory.UNAVAILABLE, summary)
    return failure(PLAYBOOK, ErrorCategory.FAILED, summary)


def responsible_update(job: Job) -> Job | None:
    """The failed project or inventory update AWX names in the job's explanation."""
    dependency = job.failed_dependency
    if dependency is None or dependency.kind not in _UPDATES:
        return None
    return Job(id=dependency.id, kind=dependency.kind, name=dependency.name, status="failed")


def finished_failure(
    job: Job,
    *,
    reasons: Sequence[str],
    status_held: bool,
    failed_tasks: Sequence[FailedTask] | None,
) -> CaseFailure:
    """The failure of a finished job whose expectations did not all hold.

    ``failed_tasks`` are the responsible execution's: the
    :func:`responsible_update` when there is one, else the job's.
    """
    if status_held or job.status == "successful":
        return failure(EXPECTATION, ErrorCategory.FAILED, "; ".join(reasons))
    update = responsible_update(job)
    if update is not None:
        system, category = _UPDATES[update.kind]
        summary = f"{update.kind.replace('_', ' ')} {update.id}"
        if update.name:
            summary += f" for {q(update.name)}"
        summary += " failed" + _first_reason(failed_tasks)
        hint = f"read its log: `untaped awx jobs logs {update.id} --kind {update.kind}`"
        if system in _HINTS:
            hint = f"{_HINTS[system]}; {hint}"
        return failure(system, category, summary, hint=hint)
    if job.status == "failed":
        return _failed_job(job, failed_tasks)
    why = job.job_explanation or _last_line(job.result_traceback)
    if job.status == "error" and _CREDENTIAL_LOOKUP.search(
        f"{job.job_explanation or ''}\n{job.result_traceback or ''}"
    ):
        return failure(CREDENTIALS, ErrorCategory.AUTH, f"job could not use a credential: {why}")
    ended = "was canceled outside this run" if job.status == "canceled" else "ended in error"
    return failure(
        CONTROLLER, ErrorCategory.UNAVAILABLE, f"job {ended}" + (f": {why}" if why else "")
    )


def _failed_job(job: Job, failed_tasks: Sequence[FailedTask] | None) -> CaseFailure:
    tasks = list(failed_tasks or ())
    if tasks and all(task.status == "unreachable" for task in tasks):
        hosts = sorted({task.host or "?" for task in tasks})
        return failure(
            HOSTS,
            ErrorCategory.UNAVAILABLE,
            "unreachable: " + ", ".join(hosts) + _first_reason(tasks),
        )
    failed = [task for task in tasks if task.status == "failed"]
    if failed:
        first = failed[0]
        summary = f"task {q(first.task or '?')} failed on {first.host or '?'}" + _first_reason(
            failed
        )
        if len(failed) > 1:
            summary += f" (and {len(failed) - 1} more)"
        return failure(PLAYBOOK, ErrorCategory.FAILED, summary)
    summary = "job failed without a failed task (a syntax error or a missing role?): see log_tail"
    if job.job_explanation:
        summary = f"job failed: {job.job_explanation}"
    return failure(PLAYBOOK, ErrorCategory.FAILED, summary)


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
