"""RunTestSuite: launch every planned case, wait for it and check it.

The cases are planned first (:mod:`~untaped_awx.application.suites.plan`):
bound to their templates, prefetched, resolved and, with a
:class:`LaunchCheck`, checked before any job runs. Each finished job is
checked against its case's :class:`Expectation` by
:mod:`~untaped_awx.application.suites.checks`, which reads what it carries
through :mod:`~untaped_awx.application.suites.evidence`. The parts call one
way: runner, then plan or checks, then evidence. An ``idempotent`` case that
passed is launched again with the same payload (on the commit the first job
ran), and the rerun must succeed without changing anything. The run is
compared with a saved baseline, or with a first pass on a baseline ref; the
caller reports :meth:`SuiteRunOutcome.deciding`, which decides the exit code,
and a run that aborts hands the failures of the cases that finished to its
``report`` callback before the error propagates. With a :class:`Canceller`,
every execution the run stops watching before it ends (timeout, polling
error, Ctrl-C) is cancelled rather than left running.

A workflow case reads its workflow through a :class:`WorkflowRun` of its own.
While it runs, the approvals it waits on (when the preflight found any) are
answered as the case says, or fail the case at once; a workflow still running
at its timeout is blamed on the node still running.

The run is given what it launches with, what it reads with and its options
(:mod:`~untaped_awx.application.suites.collaborators`).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from functools import partial
from typing import Any

from untaped.sdk import bounded_map, command_line
from untaped_awx.application.jobs.abandon_jobs import AbandonJobs
from untaped_awx.application.suites.checks import _check, _check_workflow
from untaped_awx.application.suites.collaborators import SuiteLaunching, SuiteOptions, SuiteReading
from untaped_awx.application.suites.evidence import _changed_tasks, _ensure_hosts, _gather
from untaped_awx.application.suites.plan import (
    _build_plan,
    _check_launches,
    _prefetch_plan,
    _resolve_all,
    _ResolvedCase,
)
from untaped_awx.application.suites.workflow import JobRead, WorkflowRun
from untaped_awx.domain import Job
from untaped_awx.domain.jobs.job import JOB_ROUTES
from untaped_awx.domain.suites.case_failure import (
    CaseFailure,
    FailureEvidence,
    in_node,
    request_failure,
    stalled_node_failure,
    timeout_failure,
)
from untaped_awx.domain.suites.expectations import Expectation
from untaped_awx.domain.suites.results import (
    Baseline,
    CaseResult,
    SuiteRunOutcome,
    idempotence,
    outranks_failure,
)
from untaped_awx.domain.suites.suite import Suite, TemplateBinding
from untaped_awx.domain.suites.suite_baseline import saved_baselines
from untaped_awx.domain.suites.systems import EXPECTATION
from untaped_awx.domain.suites.workflow_run import WORKFLOW_JOB, RunNode
from untaped_awx.errors import ActionResponseError, PendingApprovalError

_LAUNCH_ACTION = "launch"
_RERUN = Expectation(changed=0)
"""What an ``idempotent`` case's rerun must do: succeed without changing anything."""


class RunTestSuite:
    """Run test suites against AWX: launch every selected case, watch it and check it."""

    def __init__(
        self, launching: SuiteLaunching, reading: SuiteReading, options: SuiteOptions
    ) -> None:
        self._launching = launching
        self._reading = reading
        self._options = options
        # Built from both groups: it cancels through ``launching`` and re-reads a job
        # whose cancel was refused through ``reading``.
        self._abandon = AbandonJobs(launching.canceller, refresher=reading.job_reader.fetch)
        """Cancels (or, without a canceller, leaves) what the run stops watching."""
        self.launched: list[Job] = []
        """Executions submitted so far (for reporting after an interrupt)."""
        self.cancelled = self._abandon.cancelled
        """``(kind, id)`` of executions whose cancel AWX accepted."""
        self._finals: dict[tuple[str, int], Job] = {}

    def __call__(
        self,
        suites: Iterable[Suite],
        *,
        case_filter: set[str] | None = None,
        parallel: int = 1,
        timeout: float | None = None,
        default_timeout: float | None = None,
        scm_branch: str | None = None,
        baseline: str | None = None,
        compare: Mapping[tuple[str, str], Baseline] | None = None,
        bindings: Mapping[str, TemplateBinding] | None = None,
    ) -> SuiteRunOutcome:
        """Run the selected cases, and compare them with a baseline when given one.

        A case waits ``timeout`` when given, else its own ``timeout:``, else its
        suite's ``defaults.timeout``, else ``default_timeout`` (``None``: forever).
        ``scm_branch`` replaces every case's own. ``compare`` is a saved
        baseline; ``baseline`` is a ref every case runs on first, as the
        baseline, whose environment failures then count as this run's, each
        labelled ``suite/case (baseline)``.
        ``bindings`` binds a suite, by name, to another template than the one
        it names (a temporary copy); a pinned one gets no ``scm_branch``.
        """
        plan = _build_plan(self._launching, list(suites), case_filter, bindings or {})
        self._launching.fk_prefetcher.prefetch(_prefetch_plan(self._launching, plan))
        resolved = _resolve_all(
            self._launching, plan, timeout=timeout, default_timeout=default_timeout
        )
        run = partial(self._run_pass, resolved, parallel=parallel)
        if baseline is None:
            outcome = run(scm_branch=scm_branch)
            return (
                outcome if compare is None else outcome.compared(compare, case_filter=case_filter)
            )
        before = run(scm_branch=baseline)
        # Only the baseline's environment failures say something about this run.
        carried = [
            (f"{item} (baseline)" if item is not None else None, failure)
            for item, failure in before.deciding()
            if outranks_failure(failure)
        ]
        try:
            outcome = run(scm_branch=scm_branch)
        except Exception:
            self._note(carried)
            raise
        rows = [row.model_dump(mode="json") for row in before.results]
        return outcome.compared(saved_baselines(rows), case_filter=case_filter, carried=carried)

    def _run_pass(
        self, resolved: Sequence[_ResolvedCase], *, scm_branch: str | None, parallel: int
    ) -> SuiteRunOutcome:
        """Check, launch and watch every case once, on ``scm_branch`` when given.

        When the run aborts, the failures of the cases that finished are
        counted before the error propagates, so its exit code keeps them.
        """
        if scm_branch is not None:
            resolved = [
                item
                if item.pinned
                else replace(item, payload={**item.payload, "scm_branch": scm_branch})
                for item in resolved
            ]
        checked = _check_launches(self._launching, resolved)
        results: dict[int, CaseResult] = {}
        try:
            bounded_map(
                lambda index: self._launch_and_wait(checked[index]),
                range(len(checked)),
                concurrency=max(1, parallel),
                on_each=results.__setitem__,
                # Ctrl-C stops polling workers; queued cases are never launched.
                on_abort=self._launching.stop.set if self._launching.stop is not None else None,
            )
        except KeyboardInterrupt:
            self._abandon.unfinished(self.known_executions())
            raise
        except Exception:
            self._note(
                (f"{row.suite}/{row.case}", row.failure)
                for row in results.values()
                if row.failure is not None
            )
            raise
        # Indexed by declaration order, so the report ignores completion order.
        return SuiteRunOutcome(results=[results[index] for index in range(len(checked))])

    def _note(self, failures: Iterable[tuple[str | None, CaseFailure]]) -> None:
        """Report finished cases' failures (``suite/case``) before an abort propagates."""
        for item, failure in failures:
            self._options.report(item, failure)

    def known_executions(self) -> list[Job]:
        """Every submitted execution with its latest locally known status."""
        return [self._finals.get((job.kind, job.id), job) for job in self.launched]

    def _launch_and_wait(self, item: _ResolvedCase) -> CaseResult:
        """Launch, watch and check one case (then its ``idempotent`` rerun).

        A case that did not pass carries its ``failure``. A run that is
        stopping (Ctrl-C) launches no rerun.
        """
        started_clock = self._options.clock()
        row, job, read = self._run_case(item, item.expect)
        stopping = self._launching.stop is not None and self._launching.stop.is_set()
        if item.expect.idempotent and row.failure is None and job is not None and not stopping:
            row = self._rerun(item, row, job, read)
        return row.model_copy(update={"duration_s": self._options.clock() - started_clock})

    def _rerun(
        self, item: _ResolvedCase, first: CaseResult, job: Job, read: JobRead | None
    ) -> CaseResult:
        """Launch a case that passed once more: the rerun must succeed and change nothing.

        The rerun runs the commit ``job`` ran (the one every job of a workflow
        ran) when the case names its ref. The
        row keeps the first job and adds ``rerun_job_id``; a rerun that failed
        is attributed as any job is, and one that changed something is the
        expectation's, with the tasks it changed as evidence.
        """
        revision = job.scm_revision
        if read is not None and read.workflow is not None:
            revision = read.workflow.revision(job)
        if "scm_branch" in item.payload and revision:
            item = replace(item, payload={**item.payload, "scm_branch": revision})
        rerun, rerun_job, rerun_read = self._run_case(item, _RERUN)
        check = idempotence(rerun)
        failure = rerun.failure
        if failure is not None and failure.system == EXPECTATION.name():
            # A rerun that succeeded fails only its ``changed`` check.
            changed = None
            if self._options.evidence and rerun_job is not None:
                changed = _changed_tasks(self._reading, rerun_job, rerun_read)
            evidence = failure.evidence.model_copy(update={"changed_tasks": changed})
            failure = failure.model_copy(
                update={"message": check.describe_failure(), "evidence": evidence}
            )
        elif failure is not None:
            job_kind = "workflow job" if item.workflow else "job"
            what = "the rerun" if rerun.job_id is None else f"rerun {job_kind} {rerun.job_id}"
            failure = failure.model_copy(update={"message": f"{what}: {failure.message}"})
        return first.model_copy(
            update={
                "result": rerun.result,
                "rerun_job_id": rerun.job_id,
                "expectations": (*first.expectations, check),
                "failure": failure,
            }
        )

    def _run_case(
        self, item: _ResolvedCase, expect: Expectation
    ) -> tuple[CaseResult, Job | None, JobRead | None]:
        """Launch the case's payload once, watch the job and check it against ``expect``.

        Returns the row, the job as last read and what checking it read
        (``None`` when the launch failed).
        """
        row = partial(CaseResult, suite=item.suite_name, case=item.case_name)
        try:
            job = self._launching.launcher(
                item.spec,
                name=item.template,
                action=_LAUNCH_ACTION,
                scope=item.scope,
                payload=item.payload,
            )
            self.launched.append(job)
        except Exception as exc:
            if not (isinstance(exc, ActionResponseError) and exc.execution_id is not None):
                return row(result="error", failure=request_failure(exc, launching=True)), None, None
            # ``ignored_fields`` responses launched a job: keep its ID as evidence, abandon it.
            created = Job(id=exc.execution_id, kind=exc.execution_kind or "job", status="unknown")
            self.launched.append(created)
            message = f"{exc}; {self._abandon(created)}"
            launch_failure = request_failure(exc, launching=True, message=message)
            return row(result="error", job_id=created.id, failure=launch_failure), None, None
        read = JobRead(source=job, workflow=self._workflow_run(item))
        failure: CaseFailure | None = None
        try:
            final = self._watch_case(item, job, read.workflow)
            self._finals[(final.kind, final.id)] = final
        except Exception as exc:
            final = job
            fields: dict[str, Any] = {"result": "error"}
            failure = self._abandoned(job, exc)
        else:
            fields = {}
            if final.is_terminal:
                try:
                    # Failed tasks and host summaries are only complete once saved.
                    final = self._finals[(final.kind, final.id)] = self._reading.job_reader.settled(
                        final
                    )
                except Exception as exc:
                    fields["result"] = "error"
                    failure = request_failure(exc)
            fields |= {
                "job_status": final.status,
                "started_at": final.started_at,
                "finished_at": final.finished_at,
                "scm_branch": final.scm_branch,
                "scm_revision": final.scm_revision,
            }
            if failure is None:
                failure = self._finish(item, expect, final, fields, read)
                final = self._finals.get((final.kind, final.id), final)
        if failure is not None and self._options.evidence:
            failure = _gather(self._reading, final, failure, read)
        result = row(
            job_id=final.id, job_url=self._reading.job_url(final), failure=failure, **fields
        )
        return result, final, read

    def _workflow_run(self, item: _ResolvedCase) -> WorkflowRun | None:
        """The reads and approvals of a workflow case's run (``None`` for a job case)."""
        if not item.workflow:
            return None
        return WorkflowRun(
            node_reader=self._reading.node_reader,
            approver=self._launching.approver,
            job_reader=self._reading.job_reader,
            host_reader=self._reading.host_reader,
            approvals=item.approvals,
        )

    def _watch_case(self, item: _ResolvedCase, job: Job, workflow: WorkflowRun | None) -> Job:
        """Watch a case's job; a workflow's approvals are answered meanwhile, if it has any."""
        if workflow is None or item.approval_nodes == ():
            return self._launching.watcher(job, timeout=item.timeout)
        return self._launching.watcher(job, timeout=item.timeout, on_state=workflow.answer)

    def _abandoned(self, job: Job, error: Exception) -> CaseFailure:
        """The failure of a case whose watch stopped on ``error``; its job is abandoned.

        A pending approval nobody answers names its node, and, when the
        workflow keeps running, how to finish it.
        """
        fate = self._abandon(job)
        if not isinstance(error, PendingApprovalError):
            return request_failure(error, message=f"{error}; {fate}")
        if not self._abandon.cancels:
            # Lazy: the hint spells awx's command, and the commands import this module.
            from untaped_awx.commands.job_actions import Cancel  # noqa: PLC0415

            cancel = command_line(Cancel.spelled(job.id, "--kind", job.kind))
            fate += (
                f": approve or deny workflow approval {error.approval_id} in AWX, or cancel it "
                f"with `{cancel}`"
            )
        found = request_failure(error, message=f"{error}; {fate}")
        return found.model_copy(update={"evidence": FailureEvidence(node=error.node)})

    def _finish(
        self,
        item: _ResolvedCase,
        expect: Expectation,
        job: Job,
        fields: dict[str, Any],
        read: JobRead,
    ) -> CaseFailure | None:
        """Check a finished job, or give up on one still running at the case's timeout.

        A workflow's host summaries (its node jobs', summed) are read only for a check.
        """
        if expect.needs_hosts or (self._options.hosts and job.kind != WORKFLOW_JOB):
            _ensure_hosts(self._reading, job, fields, read)
        if job.is_terminal and job.kind == WORKFLOW_JOB:
            return _check_workflow(
                self._reading, self._options, job, expect, expect.nodes, fields, read, depth=0
            )
        if job.is_terminal:
            return _check(self._reading, job, expect, fields, read)
        waited = f"after {item.timeout or 0:g}s"
        stalled = self._stalled(job, read, fields)
        fate = self._abandon(job)
        if stalled is None:
            failure = timeout_failure(job, f"still {job.status} {waited}; {fate}")
        else:
            path, node = stalled
            failure = stalled_node_failure(node, f"still {node.status} {waited}; {fate}")
            execution = node.execution
            if self._options.evidence and execution is not None and node.kind in JOB_ROUTES:
                failure = _gather(self._reading, execution, failure, JobRead(source=execution))
            failure = in_node(failure, path)
        fields["result"] = "timeout"
        # A refused cancel re-reads the job: it may have ended meanwhile.
        latest = self._finals[(job.kind, job.id)] = self._abandon.latest(job)
        fields.update(job_status=latest.status, finished_at=latest.finished_at)
        return failure

    def _stalled(
        self, workflow: Job, read: JobRead, fields: dict[str, Any]
    ) -> tuple[str, RunNode] | None:
        """A workflow's node still unfinished at its timeout, and its nodes for the row."""
        if read.workflow is None:
            return None
        try:
            fields["nodes"] = tuple(node.result() for node in read.workflow.nodes(workflow))
            return read.workflow.stalled(workflow)
        except Exception:
            return None
