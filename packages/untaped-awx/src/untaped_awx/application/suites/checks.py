"""The verdict on a suite case's finished job or workflow.

A job's own outcome is attributed first (a failed update, an error, a failed
task), so a log, host summaries or events a check could not read never hide
it: each becomes a note, or the case's error when nothing else failed
(:func:`_verdict`). A workflow's checks read its node jobs; each node
expectation is checked against its node's job by the same code as a job
case, and a failed workflow is blamed on the node that failed it, recursing
into nested workflows. The checks read through
:mod:`untaped_awx.application.suites.evidence`, never the runner.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from untaped.sdk import (
    ErrorCategory,
)
from untaped_awx.application.suites.collaborators import SuiteOptions, SuiteReading
from untaped_awx.application.suites.evidence import (
    _ensure_hosts,
    _failed_tasks,
    _gather,
    _host_checks,
    _job_tasks,
    _update,
)
from untaped_awx.application.suites.workflow import (
    JobRead,
    NodeRun,
    WorkflowRun,
    status_only_failure,
)
from untaped_awx.domain import Job
from untaped_awx.domain.jobs.job import (
    JOB_ROUTES,
)
from untaped_awx.domain.suites.case_failure import (
    CaseFailure,
    FailedTask,
    approval_failure,
    events_unsaved,
    finished_failure,
    in_node,
    request_failure,
    tasks_unread,
    unproven,
    workflow_failure,
)
from untaped_awx.domain.suites.case_failure import (
    failure as make_failure,
)
from untaped_awx.domain.suites.expectations import (
    ExecutionChecks,
    Expectation,
    ExpectationResult,
    NodeExpectation,
)
from untaped_awx.domain.suites.systems import CONTROLLER, PLAYBOOK, SUITE
from untaped_awx.domain.suites.workflow_run import (
    APPROVAL,
    MAX_NESTING,
    NEVER_RAN,
    WORKFLOW_JOB,
    RunNode,
    blamed_node,
)


def _check(
    reading: SuiteReading,
    job: Job,
    expect: ExecutionChecks,
    fields: dict[str, Any],
    read: JobRead,
) -> CaseFailure | None:
    """Check a finished job against ``expect``; set ``result`` and ``expectations``.

    The job's own outcome is attributed first (a failed update, an error,
    a failed task), so a log, host summaries or events a check could not
    read never hide it: each becomes a note, or the case's error when
    nothing else failed.
    """
    status = expect.check_status(job.status)
    checks = [status]
    unread: list[CaseFailure] = []
    # AWX writes the log, host summaries and failed tasks from the job's events.
    saved = job.event_processing_finished is not False or not expect.checks_beyond_status
    if not saved:
        unread.append(events_unsaved(job))
    if expect.needs_log and saved:
        try:
            read.log = reading.log_reader(job)
        except Exception as exc:
            unread.append(request_failure(exc, message=f"log fetch failed: {exc}"))
        else:
            checks.extend(expect.log.evaluate(read.log))
    if expect.needs_hosts and saved:
        host_checks = _host_checks(reading, job, expect, fields, read)
        if isinstance(host_checks, CaseFailure):
            unread.append(host_checks)
        else:
            checks.extend(host_checks)
    update = _update(reading, job, read)
    read.source = update or job
    if expect.failed_tasks and update is None and saved:
        task_checks = _task_checks(job, expect, _job_tasks(reading, job, fields, read))
        if isinstance(task_checks, CaseFailure):
            unread.append(task_checks)
        else:
            checks.extend(task_checks)
    fields["expectations"] = tuple(checks)
    reasons = [check.describe_failure() for check in checks if not check.passed]
    if job.status != "successful" and not read.tasks_read and (update is not None or reasons):
        if update is None:
            _job_tasks(reading, job, fields, read)
        else:
            read.tasks, read.tasks_read = _failed_tasks(reading, update, None), True
    failure = finished_failure(
        job,
        update=update,
        update_url=reading.job_url(update) if update is not None else None,
        status_held=status.passed,
        reasons=reasons,
        failed_tasks=read.tasks,
    )
    return _verdict(failure, unread, fields)


def _check_workflow(
    reading: SuiteReading,
    options: SuiteOptions,
    workflow: Job,
    expect: ExecutionChecks,
    node_expects: Mapping[str, NodeExpectation],
    fields: dict[str, Any],
    read: JobRead,
    *,
    depth: int,
) -> CaseFailure | None:
    """Check a finished workflow (nested ``depth`` levels) and its nodes ``node_expects``.

    The workflow's own checks read its node jobs (summed host summaries,
    the failed tasks of the nodes that failed); each node expectation is
    checked against its node's job, and a failed workflow is attributed
    to the node that failed it. Nodes that cannot be read make the case an
    error: nothing is checked against them.
    """
    assert read.workflow is not None  # a workflow's read has its run
    run = read.workflow
    status = expect.check_status(workflow.status)
    try:
        nodes = run.nodes(workflow)
    except Exception as exc:
        fields["expectations"] = (status,)
        unreadable = request_failure(exc, message=f"workflow nodes unreadable: {exc}")
        return _verdict(None, [unreadable], fields)
    fields["nodes"] = tuple(node.result() for node in nodes)
    checks = [status]
    unread: list[CaseFailure] = []
    if expect.needs_log:
        message = "a workflow job has no log; check the log of a node under expect.nodes"
        unread.append(make_failure(SUITE, ErrorCategory.INVALID, message))
    unsaved = _unsaved_node_job(workflow, expect, run)
    saved = unsaved is None
    if unsaved is not None:
        unread.append(unsaved)
    if expect.needs_hosts and saved:
        host_checks = _host_checks(reading, workflow, expect, fields, read)
        if isinstance(host_checks, CaseFailure):
            unread.append(host_checks)
        else:
            checks.extend(host_checks)
    node_failures: list[CaseFailure] = []
    for node_id, node_expect in node_expects.items():
        node_checks, node_failure = _check_node(
            reading, options, node_id, node_expect, nodes, run, depth
        )
        checks.extend(node_checks)
        if node_failure is None:
            continue
        if all(check.passed for check in node_checks):
            unread.append(node_failure)  # a check it could not make, not one that failed
        else:
            node_failures.append(node_failure)
    if expect.failed_tasks and saved:
        task_checks = _task_checks(workflow, expect, _workflow_tasks(reading, workflow, run))
        if isinstance(task_checks, CaseFailure):
            unread.append(task_checks)
        else:
            checks.extend(task_checks)
    fields["expectations"] = tuple(checks)
    reasons = [check.describe_failure() for check in checks if not check.passed]
    culprit = None
    if workflow.status != "successful":
        culprit = _culprit(reading, options, nodes, run, depth)
    found = workflow_failure(
        workflow,
        culprit=culprit,
        status_held=status.passed,
        reasons=reasons,
        node_failures=node_failures,
    )
    return _verdict(found, unread, fields)


def _check_node(
    reading: SuiteReading,
    options: SuiteOptions,
    node_id: str,
    expect: NodeExpectation,
    nodes: Sequence[RunNode],
    run: WorkflowRun,
    depth: int,
) -> tuple[list[ExpectationResult], CaseFailure | None]:
    """A node expectation's checks (each naming the node), and the node's failure if any.

    A node that never ran has only its status; so do an approval and a
    management job. A node that ran a job is checked like a job case.
    """
    node = next((node for node in nodes if node.identifier == node_id), None)
    found: CaseFailure | None
    if node is None or node.job_id is None or node.kind not in JOB_ROUTES:
        checks = [expect.check_status(node.status if node is not None else NEVER_RAN)]
        found = status_only_failure(node, expect, checks[0])
    else:
        try:
            node_run = run.run(node)
        except Exception as exc:
            checks = [expect.check_status(node.status)]
            found = request_failure(exc, message=f"job {node.job_id} unreadable: {exc}")
        else:
            if expect.needs_hosts:
                _ensure_hosts(reading, node_run.job, node_run.fields, node_run.read)
            found = _check_node_job(reading, options, node_run, expect, depth)
            checks = list(node_run.fields["expectations"])
    tagged = [check.model_copy(update={"node": node_id}) for check in checks]
    return tagged, None if found is None else in_node(found, node_id)


def _check_node_job(
    reading: SuiteReading,
    options: SuiteOptions,
    node_run: NodeRun,
    expect: ExecutionChecks,
    depth: int,
) -> CaseFailure | None:
    """A node's job (a nested workflow's too) checked against ``expect``, with evidence."""
    job = node_run.job
    if job.kind == WORKFLOW_JOB:
        if depth >= MAX_NESTING:
            message = (
                f"workflow job {job.id} ended {job.status}; workflows nested more than "
                f"{MAX_NESTING} deep are not followed"
            )
            node_run.fields["expectations"] = (expect.check_status(job.status),)
            return make_failure(PLAYBOOK, ErrorCategory.FAILED, message)
        return _check_workflow(
            reading, options, job, expect, {}, node_run.fields, node_run.read, depth=depth + 1
        )
    found = _check(reading, job, expect, node_run.fields, node_run.read)
    if found is not None and options.evidence:
        found = _gather(reading, job, found, node_run.read)
    return found


def _culprit(
    reading: SuiteReading,
    options: SuiteOptions,
    nodes: Sequence[RunNode],
    run: WorkflowRun,
    depth: int,
) -> CaseFailure | None:
    """The failure of the node that failed the workflow, by the rules for its job."""
    node = blamed_node(nodes)
    if node is None or node.job_id is None:
        return None
    if node.kind == APPROVAL:
        denial = approval_failure(node.template, node.job_id, denied=run.denied(node.job_id))
        return in_node(denial, node.label)
    if node.kind not in JOB_ROUTES:
        message = f"{node.kind} {node.job_id} ended {node.status}"
        return in_node(make_failure(CONTROLLER, ErrorCategory.UNAVAILABLE, message), node.label)
    try:
        node_run = run.run(node)
    except Exception as exc:
        message = f"job {node.job_id} unreadable: {exc}"
        return in_node(request_failure(exc, message=message), node.label)
    found = _check_node_job(reading, options, node_run, Expectation(), depth)
    return None if found is None else in_node(found, node.label)


def _unsaved_node_job(
    workflow: Job, expect: ExecutionChecks, run: WorkflowRun
) -> CaseFailure | None:
    """Why the workflow's checks cannot read its node jobs yet: one's events are still saving.

    Host and ``failed_tasks`` checks read the node jobs' summaries and events.
    """
    if not (expect.needs_hosts or expect.failed_tasks):
        return None
    try:
        found = run.unsaved(workflow)
    except Exception as exc:
        return request_failure(exc, message=f"workflow node jobs unreadable: {exc}")
    if found is None:
        return None
    path, job = found
    return in_node(events_unsaved(job), path)


def _workflow_tasks(
    reading: SuiteReading, workflow: Job, run: WorkflowRun
) -> tuple[FailedTask, ...] | None:
    """The failed tasks of every node job that failed (nested ones too); ``None``: unread."""
    tasks: list[FailedTask] = []
    try:
        for _, node, execution in run.node_jobs(workflow):
            if not node.failed or execution.kind not in JOB_ROUTES:
                continue
            if execution.kind == WORKFLOW_JOB:
                continue  # its own nodes follow
            node_run = run.run(node)
            found = _job_tasks(reading, node_run.job, node_run.fields, node_run.read)
            if found is None:
                return None
            tasks.extend(found)
    except Exception:
        return None
    return tuple(tasks)


def _verdict(
    found: CaseFailure | None, unread: Sequence[CaseFailure], fields: dict[str, Any]
) -> CaseFailure | None:
    """``found``, with what could not be read as its note; set ``result``.

    With nothing else wrong, the first unread problem is the case's error.
    """
    if found is None and unread:
        fields["result"] = "error"
        found, *rest = unread
    else:
        fields["result"] = "pass" if found is None else "fail"
        rest = list(unread)
    if found is not None and rest:
        note = "; ".join(problem.message for problem in rest)
        found = found.model_copy(
            update={"evidence": found.evidence.model_copy(update={"note": note})}
        )
    return found


def _task_checks(
    execution: Job, expect: ExecutionChecks, tasks: Sequence[FailedTask] | None
) -> list[ExpectationResult] | CaseFailure:
    """The ``failed_tasks`` checks, or why they cannot be decided.

    They cannot when the tasks are unread, or when an entry is matched only
    by a failed task a rescue may have handled.
    """
    if tasks is None:
        return tasks_unread(execution)
    unsure = expect.unproven_match(tasks)
    if unsure is not None:
        return unproven(unsure)
    return expect.check_failed_tasks(tasks)
