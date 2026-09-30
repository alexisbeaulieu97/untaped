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
  not yet readable; a check needed its events while AWX was still saving
  them, or a ``failed_tasks`` entry is matched only by a failure a rescue may
  have handled; or it never left ``pending``/``waiting`` before the timeout.
- ``awx.scm`` / ``awx.inventory``: AWX names a failed project or inventory
  update in ``Previous Task Failed: {…}``, even when the case expected the
  job to fail (a ref not overridable, found before launching, is
  ``awx.scm`` too).
- ``awx.expectation``: the job ran its playbook as asked, but an expectation
  did not hold.
- ``awx.hosts``: the job failed and every failed task is an unreachable host.
- ``awx.playbook``: the job failed any other way, or a timeout while it ran.

A failure whose error is not AWX's (an untaped bug, local setup) keeps its
own system, and so does one already attributed to an ``awx.*`` system. The
runner reads the job, the update, the failed tasks and the responsible
execution's log; this module decides.

A workflow is attributed to the node that failed it (:func:`workflow_failure`):
the node's job is attributed by the rules above and the failure is prefixed
``node <id>:`` (:func:`in_node`). An approval node the case denied is the
expectation's; one denied or timed out outside the run is the controller's.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from untaped.capabilities.awx.domain.job import HostSummary, Job, JobEvent
from untaped.capabilities.awx.domain.workflow_run import APPROVAL, RunNode
from untaped.capability_api import ErrorCategory, ErrorInfo, UntapedError, q

SUITE = "awx.suite"
CREDENTIALS = "awx.credentials"
CONTROLLER = "awx.controller"
SCM = "awx.scm"
INVENTORY = "awx.inventory"
HOSTS = "awx.hosts"
PLAYBOOK = "awx.playbook"
EXPECTATION = "awx.expectation"

FAILED_TASK_EVENTS = ("runner_on_failed", "runner_on_unreachable")
"""Job events that end a task on a host in failure.

An async task that fails also sends ``runner_on_async_failed`` first, which
AWX flags failed even under ``ignore_errors``; its ``runner_on_failed`` follows.
"""

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
UPDATE_KINDS = frozenset(_UPDATES)
"""Execution kinds of the updates a job waits for (a workflow node may run one itself)."""
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
APPROVALS_HINT = "set `approvals: approve` or `approvals: deny` on the case (or in its defaults)"
"""What to do about an approval a workflow case gave no answer for."""
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
    unsure: bool = Field(default=False, exclude=True)
    """Its host's summary cannot show that no ``rescue`` (or ``ignore_unreachable``) handled it."""

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
    node: str | None = None
    """The workflow node whose job the evidence is from (``outer/inner`` in a nested workflow)."""

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

    An error that is not AWX's, or already names an ``awx.*`` system (a
    pending approval is the suite's), keeps its own system; a rejected token or
    permission is the credentials'; a refused launch is the suite's (the
    scm's for an ``scm_branch`` the template does not prompt for); anything
    else is the controller's.
    """
    info = ErrorInfo.from_exception(error)
    if info.system != "awx":
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


def stalled_node_failure(node: RunNode, message: str) -> CaseFailure:
    """A workflow still unfinished at its timeout, blamed on ``node``, still unfinished too.

    An approval still waiting is the suite's (nothing answered it); a job is
    blamed as a job case's at its timeout.
    """
    execution = node.execution
    if node.kind == APPROVAL or execution is None:
        what = f"approval {q(node.template or node.job_id)} is still waiting: {message}"
        return failure(SUITE, ErrorCategory.INVALID, what, hint=APPROVALS_HINT)
    return timeout_failure(execution, message)


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


def unhandled(
    events: Iterable[JobEvent], hosts: Mapping[str, HostSummary] | None
) -> tuple[FailedTask, ...]:
    """The failed tasks of a job's ``events`` that nothing handled, in the order they ran.

    Ansible reports a task a ``rescue`` block handled as failed, and so does
    AWX's event; only the host's summary tells them apart, counting it
    ``rescued`` rather than ``failed``. An unhandled failure ends the host's
    play (only ``always`` sections and handlers still run, and a failure
    there is unhandled too), so a host that counts ``failed: N`` failed on its
    last N failed tasks, and the ones before were rescued. Likewise its last
    ``unreachable`` ones found it unreachable; the ones before were let
    through by ``ignore_unreachable``. When a host has no summary (or
    ``hosts`` is ``None``: none were read) or its counters do not account for
    its failed tasks, they are all kept, marked ``unsure``.
    """
    failed = sorted(
        (event for event in events if event.failed and event.event in FAILED_TASK_EVENTS),
        key=lambda event: event.counter,
    )
    tasks = [FailedTask.from_event(event) for event in failed]
    groups: dict[tuple[str | None, str], list[int]] = {}
    for index, task in enumerate(tasks):
        groups.setdefault((task.host, task.status), []).append(index)
    handled: set[int] = set()
    unsure: set[int] = set()
    for (host, status), indexes in groups.items():
        summary = None if hosts is None or host is None else hosts.get(host)
        if summary is None:
            unsure.update(indexes)
            continue
        if status == "failed":
            counted, handled_most = summary.failed, summary.rescued
        else:
            counted, handled_most = summary.unreachable, summary.ignored
        if counted <= len(indexes) <= counted + handled_most:
            handled.update(indexes[: len(indexes) - counted])
        else:
            unsure.update(indexes)
    return tuple(
        task.model_copy(update={"unsure": True}) if index in unsure else task
        for index, task in enumerate(tasks)
        if index not in handled
    )


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


def workflow_failure(
    workflow: Job,
    *,
    culprit: CaseFailure | None,
    status_held: bool,
    reasons: Sequence[str],
    node_failures: Sequence[CaseFailure],
) -> CaseFailure | None:
    """The failure of a finished workflow, or ``None`` when the case passed.

    ``culprit`` is the failure of the node that failed the workflow (see
    :func:`in_node`), ``reasons`` the expectations that did not hold (a
    node's prefixed with it), and ``node_failures`` those of the nodes whose
    own expectations did not hold. A failed update in the culprit wins, as it
    does for a job; a workflow that ran as the case asked fails on a node that
    did not run as asked, else on the expectation.
    """
    if culprit is not None and culprit.evidence.related is not None:
        return culprit
    if not reasons:
        return None
    if workflow.status == "canceled" or (workflow.status == "error" and culprit is None):
        return _ended_failure(workflow, "workflow job")
    if status_held or workflow.status == "successful":
        decided = next((found for found in node_failures if found.system != EXPECTATION), None)
        return decided or failure(EXPECTATION, ErrorCategory.FAILED, "; ".join(reasons))
    if culprit is None:
        why = f": {workflow.job_explanation}" if workflow.job_explanation else ""
        message = f"workflow job failed, but no failed node explains it{why}"
        return failure(CONTROLLER, ErrorCategory.UNAVAILABLE, message)
    if culprit.system == EXPECTATION:
        return culprit.model_copy(update={"message": "; ".join([culprit.message, *reasons])})
    return culprit


def in_node(found: CaseFailure, node: str) -> CaseFailure:
    """``found``, the failure of a workflow node's job, as the workflow's: named after ``node``.

    A failure already inside a nested workflow's node keeps one full path:
    ``node outer/inner: …``.
    """
    inner = found.evidence.node
    path = node if inner is None else f"{node}/{inner}"
    message = found.message if inner is None else found.message.removeprefix(f"node {inner}: ")
    evidence = found.evidence.model_copy(update={"node": path})
    return found.model_copy(update={"message": f"node {path}: {message}", "evidence": evidence})


def approval_failure(name: str | None, approval_id: int, *, denied: bool) -> CaseFailure:
    """An approval node that failed its workflow: ``denied`` by the case, or else outside it."""
    what = f"approval {q(name or approval_id)}"
    if denied:
        message = (
            f"{what} was denied as the case asked (approvals: deny), "
            "and no failure path leads out of it"
        )
        return failure(EXPECTATION, ErrorCategory.FAILED, message)
    message = f"{what} (workflow approval {approval_id}) was denied outside this run, or timed out"
    return failure(CONTROLLER, ErrorCategory.UNAVAILABLE, message)


def tasks_unread(job: Job) -> CaseFailure:
    """A finished job whose ``failed_tasks`` expectation could not be checked."""
    message = f"failed_tasks not checked: {_events_unread(job)}"
    return failure(CONTROLLER, ErrorCategory.UNAVAILABLE, message)


def events_unsaved(job: Job) -> CaseFailure:
    """A finished job whose checks read its events, which AWX was still saving.

    AWX writes a job's log, host summaries and failed tasks from its events,
    so until it has saved them all, an empty or short read proves nothing.
    """
    message = (
        f"AWX is still saving the events of {job.kind.replace('_', ' ')} {job.id}, so its log, "
        "host summaries and failed tasks are incomplete: only its status was checked"
    )
    hint = "retry later, once AWX has saved the job's events"
    return failure(CONTROLLER, ErrorCategory.UNAVAILABLE, message, hint=hint)


def unproven(task: FailedTask) -> CaseFailure:
    """A ``failed_tasks`` entry only an ``unsure`` failed task matches: it may have been handled."""
    message = (
        f"failed_tasks not proven: task {q(task.task or '?')} on {task.host or '?'} matches, but "
        "AWX's host summaries do not show whether a rescue block handled it"
    )
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
