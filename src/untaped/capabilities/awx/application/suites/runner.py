"""RunTestSuite: load → plan → prefetch → resolve → launch+wait.

With a :class:`LaunchCheck`, every case's launch is checked before any job
runs. Each finished job is checked against its case's :class:`Expectation`
(status, then log checks read through a :class:`LogReader`, host bounds read
from its host summaries, completed with filtered reads past the 500-host cut,
failed tasks from its events; none of them while AWX is still saving its
events, which makes the case an error). An ``idempotent`` case that passed is
launched again with the same payload (on the commit the first job ran), and
the rerun must succeed without changing anything. A case that does not pass carries a
:class:`CaseFailure`: the system responsible, decided by the rules in
:mod:`untaped.capabilities.awx.domain.case_failure` from the job, its
explanation and its failed tasks (from job events), and, with ``evidence``,
what shows it: the failed tasks and log tail of the responsible execution (the
project or inventory update that failed first, else the job; a tail reads only
the newest events), and the tasks a rerun changed. The run is compared with a
saved baseline, or with a first pass on a baseline ref; the caller counts
:meth:`SuiteRunOutcome.counted` toward the exit code, and a run that aborts
counts the failures of the cases that finished before it propagates. With
``hosts``, every case with a job carries its hosts' summaries; a case whose
checks need them reads them anyway. With a :class:`Canceller`, every
execution the run stops watching before it ends (timeout, polling error,
Ctrl-C) is cancelled rather than left running. A failed preflight carries its
most severe problem's category and the system responsible.

A workflow case reads its workflow through a :class:`WorkflowRun` of its own.
While it runs, the approvals it waits on (when the preflight found any) are
answered as the case says, or fail the case at once. Once it ended, each node
expectation is checked against its node's job by the same code as a job case,
the workflow's host checks bound its node jobs' summed summaries, and a failed
workflow is blamed on the node that failed it, recursing into nested
workflows; a workflow still running at its timeout is blamed on the node still
running.

Resolution finishes in the main thread before any worker is spawned so
the launch+wait pool only sees fully-baked, immutable launch dicts —
keeps workers free of FK lookups entirely. (:class:`FkResolver`'s caches
are thread-safe per issue #208, but skipping the lock dance is still
cleaner than racing workers through it.)
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from functools import partial
from itertools import islice
from typing import Any

from untaped.capabilities.awx.application.abandon_jobs import AbandonJobs
from untaped.capabilities.awx.application.ports import Canceller
from untaped.capabilities.awx.application.suites.ports import (
    ApprovalDecider,
    EventReader,
    FkPrefetcher,
    HostReader,
    JobReader,
    LaunchCheck,
    Launcher,
    LogReader,
    NodeReader,
    TailReader,
    Watcher,
)
from untaped.capabilities.awx.application.suites.preflight import refused
from untaped.capabilities.awx.application.suites.resolver import ResolveCasePayload
from untaped.capabilities.awx.application.suites.workflow import (
    JobRead,
    NodeRun,
    WorkflowRun,
    status_only_failure,
)
from untaped.capabilities.awx.domain import Job, ResourceSpec
from untaped.capabilities.awx.domain.case_failure import (
    CONTROLLER,
    EXPECTATION,
    FAILED_TASK_EVENTS,
    PLAYBOOK,
    SUITE,
    UPDATE_KINDS,
    CaseFailure,
    ChangedTask,
    FailedTask,
    FailureEvidence,
    approval_failure,
    events_unsaved,
    finished_failure,
    in_node,
    request_failure,
    responsible_update,
    stalled_node_failure,
    tasks_unread,
    timeout_failure,
    unhandled,
    unproven,
    workflow_failure,
)
from untaped.capabilities.awx.domain.case_failure import (
    failure as make_failure,
)
from untaped.capabilities.awx.domain.job import (
    JOB_ROUTES,
    HostSummary,
    by_host,
    host_summaries,
)
from untaped.capabilities.awx.domain.suite import (
    WORKFLOW_TEMPLATE,
    Approvals,
    Baseline,
    Case,
    CaseResult,
    ExecutionChecks,
    Expectation,
    ExpectationResult,
    NodeExpectation,
    RefSentinel,
    Suite,
    SuiteRunOutcome,
    TemplateBinding,
    idempotence,
    outranks_failure,
    select_cases,
)
from untaped.capabilities.awx.domain.suite_baseline import saved_baselines
from untaped.capabilities.awx.domain.workflow_run import (
    APPROVAL,
    MAX_NESTING,
    NEVER_RAN,
    WORKFLOW_JOB,
    RunNode,
    blamed_node,
)
from untaped.capabilities.awx.errors import ActionResponseError, AwxApiError, PendingApprovalError
from untaped.capability_api import (
    ConfigError,
    ErrorCategory,
    UntapedError,
    bounded_map,
    note_failure,
)

_LAUNCH_ACTION = "launch"
LOG_TAIL_LINES = 40
"""Log lines a case that did not pass carries in its result."""
CHANGED_TASKS_LIMIT = 100
"""Changed tasks a rerun that was not idempotent lists in its evidence."""
_CHANGED_TASKS = {"event": "runner_on_ok", "changed": "true"}
"""The events of tasks that changed a host (a loop's items fold into their task's)."""
_RERUN = Expectation(changed=0)
"""What an ``idempotent`` case's rerun must do: succeed without changing anything."""


@dataclass(frozen=True, slots=True)
class _ResolvedCase:
    suite_name: str
    case_name: str
    spec: ResourceSpec
    template: str
    scope: dict[str, str] | None
    payload: dict[str, Any]
    expect: Expectation
    timeout: float | None
    approvals: Approvals | None = None
    approval_nodes: tuple[str, ...] | None = None
    """A workflow's approval nodes, as the preflight found them (``None``: not looked for)."""
    pinned: bool = False
    """The template runs the tested commit itself: it gets no launch-time ``scm_branch``."""

    @property
    def workflow(self) -> bool:
        return self.spec.kind == WORKFLOW_TEMPLATE


class RunTestSuite:
    def __init__(
        self,
        *,
        resolver: ResolveCasePayload,
        launcher: Launcher,
        watcher: Watcher,
        specs: Callable[[str], ResourceSpec],
        fk_prefetcher: FkPrefetcher,
        log_reader: LogReader,
        event_reader: EventReader,
        tail_reader: TailReader,
        job_url: Callable[[Job], str | None],
        jt_scope: dict[str, str] | None = None,
        clock: Callable[[], float] = time.monotonic,
        stop: threading.Event | None = None,
        job_reader: JobReader,
        host_reader: HostReader,
        node_reader: NodeReader,
        approver: ApprovalDecider,
        canceller: Canceller | None = None,
        preflight: LaunchCheck | None = None,
        evidence: bool = True,
        hosts: bool = False,
    ) -> None:
        self._resolve = resolver
        self._launch = launcher
        self._watch = watcher
        self._specs = specs
        """The spec of a template kind (``JobTemplate``, ``WorkflowJobTemplate``)."""
        self._fk = fk_prefetcher
        self._jt_scope = jt_scope
        self._clock = clock
        self._stop = stop
        self._reader = job_reader
        self._abandon = AbandonJobs(canceller, refresher=job_reader.fetch)
        """Cancels (or, without ``canceller``, leaves) what the run stops watching."""
        self._read_log = log_reader
        self._read_events = event_reader
        self._read_tail = tail_reader
        self._job_url = job_url
        self._preflight = preflight
        self._evidence = evidence
        """Attach the evidence (failed tasks, log tail) to cases that did not pass."""
        self._read_hosts = host_reader
        self._read_nodes = node_reader
        self._decide = approver
        self._hosts = hosts
        """Report every case's host summaries (a failed job's are read anyway)."""
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
        baseline, whose environment failures then count as this run's.
        ``bindings`` binds a suite, by name, to another template than the one
        it names (a temporary copy); a pinned one gets no ``scm_branch``.
        """
        plan = self._build_plan(list(suites), case_filter, bindings or {})
        self._fk.prefetch(self._prefetch_plan(plan))
        resolved = self._resolve_all(plan, timeout=timeout, default_timeout=default_timeout)
        run = partial(self._run_pass, resolved, parallel=parallel)
        if baseline is None:
            outcome = run(scm_branch=scm_branch)
            return (
                outcome if compare is None else outcome.compared(compare, case_filter=case_filter)
            )
        before = run(scm_branch=baseline)
        try:
            outcome = run(scm_branch=scm_branch)
        except Exception:
            _note(before.counted())
            raise
        rows = [row.model_dump(mode="json") for row in before.results]
        carried = [failure for failure in before.counted() if outranks_failure(failure)]
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
        checked = self._check_launches(resolved)
        results: dict[int, CaseResult] = {}
        try:
            bounded_map(
                lambda index: self._launch_and_wait(checked[index]),
                range(len(checked)),
                concurrency=max(1, parallel),
                on_each=results.__setitem__,
                # Ctrl-C stops polling workers; queued cases are never launched.
                on_abort=self._stop.set if self._stop is not None else None,
            )
        except KeyboardInterrupt:
            self._abandon.unfinished(self.known_executions())
            raise
        except Exception:
            _note(row.failure for row in results.values() if row.failure is not None)
            raise
        # Indexed by declaration order, so the report ignores completion order.
        return SuiteRunOutcome(results=[results[index] for index in range(len(checked))])

    def known_executions(self) -> list[Job]:
        """Every submitted execution with its latest locally known status."""
        return [self._finals.get((job.kind, job.id), job) for job in self.launched]

    def _build_plan(
        self,
        suites: Sequence[Suite],
        case_filter: set[str] | None,
        bindings: Mapping[str, TemplateBinding],
    ) -> list[tuple[Suite, TemplateBinding, str, Case]]:
        """Every case, or those ``case_filter`` names as ``case`` or ``suite/case``.

        Each suite is bound once: to ``bindings``' template for it, else to its own.
        """
        bound = {suite.name: suite.binding(self._jt_scope, bindings) for suite in suites}
        return [
            (suite, bound[suite.name], case_name, case)
            for suite, case_name, case in select_cases(suites, case_filter)
        ]

    def _prefetch_plan(
        self, plan: Sequence[tuple[Suite, TemplateBinding, str, Case]]
    ) -> dict[str, list[dict[str, str] | None]]:
        """Walk every case (defaults included) to learn which name lookups will fire.

        Returns a mapping suitable for :meth:`FkResolver.prefetch`, with
        the **same scope** the resolver will use for the actual lookup so
        the cache hits. Empty when no FK names appear (so prefetch is a
        no-op rather than firing spurious ``list`` calls).

        Resolution is **top-level on declared FK fields, plus any
        :class:`RefSentinel` discovered anywhere in the tree** —
        mirroring :class:`ResolveCasePayload`. Opaque user content under
        ``extra_vars`` is *not* otherwise inspected.
        """
        by_kind: dict[str, list[dict[str, str] | None]] = {}
        for suite, binding, _, case in plan:
            fk_index = ResolveCasePayload.fk_index_for(self._specs(binding.kind))
            merged = _merge_top_level(suite.defaults, case)
            org = suite.organization
            for field, value in merged.items():
                ref = fk_index.get(field)
                if ref is not None and _is_resolvable_fk_value(value):
                    assert ref.kind is not None
                    scope = self._resolve.scope_for_fk_field(ref, organization=org)
                    by_kind.setdefault(ref.kind, []).append(scope)
                _collect_ref_sentinels(
                    value, by_kind, partial(self._resolve.scope_for_ref, organization=org)
                )
        return by_kind

    def _resolve_all(
        self,
        plan: Sequence[tuple[Suite, TemplateBinding, str, Case]],
        *,
        timeout: float | None,
        default_timeout: float | None,
    ) -> list[_ResolvedCase]:
        out: list[_ResolvedCase] = []
        for suite, binding, case_name, case in plan:
            defaults = suite.defaults or Case()
            spec = self._specs(binding.kind)
            payload = self._resolve(
                spec, case, defaults=suite.defaults, organization=suite.organization
            )
            if binding.pinned:
                payload.pop("scm_branch", None)
            case_timeout = timeout or case.timeout or defaults.timeout or default_timeout
            out.append(
                _ResolvedCase(
                    suite.name,
                    case_name,
                    spec,
                    binding.name,
                    binding.scope,
                    payload,
                    suite.expectation(case_name),
                    case_timeout,
                    suite.approvals(case_name),
                    pinned=binding.pinned,
                )
            )
        return out

    def _check_launches(self, resolved: Sequence[_ResolvedCase]) -> list[_ResolvedCase]:
        """Raise, listing every case AWX would reject or half-ignore, before any launch.

        Each workflow case learns its workflow's approval nodes.
        """
        if self._preflight is None:
            return list(resolved)
        checked: list[_ResolvedCase] = []
        problems: list[tuple[str, UntapedError]] = []
        for item in resolved:
            try:
                launch = self._preflight(
                    item.spec,
                    name=item.template,
                    scope=item.scope,
                    payload=item.payload,
                    nodes=tuple(item.expect.nodes),
                )
                item = replace(item, payload=launch)
                if item.workflow:
                    gates = self._preflight.approval_nodes(
                        item.spec, name=item.template, scope=item.scope
                    )
                    item = replace(item, approval_nodes=tuple(gates))
            except (AwxApiError, ConfigError) as exc:
                problems.append((f"{item.suite_name}/{item.case_name}", exc))
            checked.append(item)
        if problems:
            raise refused("preflight failed, nothing launched:", problems)
        return checked

    def _launch_and_wait(self, item: _ResolvedCase) -> CaseResult:
        """Launch, watch and check one case (then its ``idempotent`` rerun).

        A case that did not pass carries its ``failure``. A run that is
        stopping (Ctrl-C) launches no rerun.
        """
        started_clock = self._clock()
        row, job, read = self._run_case(item, item.expect)
        stopping = self._stop is not None and self._stop.is_set()
        if item.expect.idempotent and row.failure is None and job is not None and not stopping:
            row = self._rerun(item, row, job, read)
        return row.model_copy(update={"duration_s": self._clock() - started_clock})

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
        if failure is not None and failure.system == EXPECTATION:
            # A rerun that succeeded fails only its ``changed`` check.
            changed = None
            if self._evidence and rerun_job is not None:
                changed = self._changed_tasks(rerun_job, rerun_read)
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
            job = self._launch(
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
                    final = self._finals[(final.kind, final.id)] = self._reader.settled(final)
                except Exception as exc:
                    fields["result"] = "error"
                    failure = request_failure(exc)
            fields |= {
                "job_status": final.status,
                "started_at": final.started,
                "finished_at": final.finished,
                "scm_branch": final.scm_branch,
                "scm_revision": final.scm_revision,
            }
            if failure is None:
                failure = self._finish(item, expect, final, fields, read)
                final = self._finals.get((final.kind, final.id), final)
        if failure is not None and self._evidence:
            failure = self._gather(final, failure, read)
        result = row(job_id=final.id, job_url=self._job_url(final), failure=failure, **fields)
        return result, final, read

    def _workflow_run(self, item: _ResolvedCase) -> WorkflowRun | None:
        """The reads and approvals of a workflow case's run (``None`` for a job case)."""
        if not item.workflow:
            return None
        return WorkflowRun(
            node_reader=self._read_nodes,
            approver=self._decide,
            job_reader=self._reader,
            host_reader=self._read_hosts,
            approvals=item.approvals,
        )

    def _watch_case(self, item: _ResolvedCase, job: Job, workflow: WorkflowRun | None) -> Job:
        """Watch a case's job; a workflow's approvals are answered meanwhile, if it has any."""
        if workflow is None or item.approval_nodes == ():
            return self._watch(job, timeout=item.timeout)
        return self._watch(job, timeout=item.timeout, on_state=workflow.answer)

    def _abandoned(self, job: Job, error: Exception) -> CaseFailure:
        """The failure of a case whose watch stopped on ``error``; its job is abandoned.

        A pending approval nobody answers names its node, and, when the
        workflow keeps running, how to finish it.
        """
        fate = self._abandon(job)
        if not isinstance(error, PendingApprovalError):
            return request_failure(error, message=f"{error}; {fate}")
        if not self._abandon.cancels:
            fate += (
                f": approve or deny workflow approval {error.approval_id} in AWX, or cancel it "
                f"with `untaped awx jobs cancel {job.id} --kind {job.kind}`"
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
        if expect.needs_hosts or (self._hosts and job.kind != WORKFLOW_JOB):
            self._ensure_hosts(job, fields, read)
        if job.is_terminal and job.kind == WORKFLOW_JOB:
            return self._check_workflow(job, expect, expect.nodes, fields, read, depth=0)
        if job.is_terminal:
            return self._check(job, expect, fields, read)
        waited = f"after {item.timeout or 0:g}s"
        stalled = self._stalled(job, read, fields)
        fate = self._abandon(job)
        if stalled is None:
            failure = timeout_failure(job, f"still {job.status} {waited}; {fate}")
        else:
            path, node = stalled
            failure = stalled_node_failure(node, f"still {node.status} {waited}; {fate}")
            execution = node.execution
            if self._evidence and execution is not None and node.kind in JOB_ROUTES:
                failure = self._gather(execution, failure, JobRead(source=execution))
            failure = in_node(failure, path)
        fields["result"] = "timeout"
        # A refused cancel re-reads the job: it may have ended meanwhile.
        latest = self._finals[(job.kind, job.id)] = self._abandon.latest(job)
        fields.update(job_status=latest.status, finished_at=latest.finished)
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

    def _check(
        self,
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
                read.log = self._read_log(job)
            except Exception as exc:
                unread.append(request_failure(exc, message=f"log fetch failed: {exc}"))
            else:
                checks.extend(expect.log.evaluate(read.log))
        if expect.needs_hosts and saved:
            host_checks = self._host_checks(job, expect, fields, read)
            if isinstance(host_checks, CaseFailure):
                unread.append(host_checks)
            else:
                checks.extend(host_checks)
        update = self._update(job, read)
        read.source = update or job
        if expect.failed_tasks and update is None and saved:
            task_checks = _task_checks(job, expect, self._job_tasks(job, fields, read))
            if isinstance(task_checks, CaseFailure):
                unread.append(task_checks)
            else:
                checks.extend(task_checks)
        fields["expectations"] = tuple(checks)
        reasons = [check.describe_failure() for check in checks if not check.passed]
        if job.status != "successful" and not read.tasks_read and (update is not None or reasons):
            if update is None:
                self._job_tasks(job, fields, read)
            else:
                read.tasks, read.tasks_read = self._failed_tasks(update, None), True
        failure = finished_failure(
            job,
            update=update,
            update_url=self._job_url(update) if update is not None else None,
            status_held=status.passed,
            reasons=reasons,
            failed_tasks=read.tasks,
        )
        return _verdict(failure, unread, fields)

    def _check_workflow(
        self,
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
        unsaved = self._unsaved_node_job(workflow, expect, run)
        saved = unsaved is None
        if unsaved is not None:
            unread.append(unsaved)
        if expect.needs_hosts and saved:
            host_checks = self._host_checks(workflow, expect, fields, read)
            if isinstance(host_checks, CaseFailure):
                unread.append(host_checks)
            else:
                checks.extend(host_checks)
        node_failures: list[CaseFailure] = []
        for node_id, node_expect in node_expects.items():
            node_checks, node_failure = self._check_node(node_id, node_expect, nodes, run, depth)
            checks.extend(node_checks)
            if node_failure is None:
                continue
            if all(check.passed for check in node_checks):
                unread.append(node_failure)  # a check it could not make, not one that failed
            else:
                node_failures.append(node_failure)
        if expect.failed_tasks and saved:
            task_checks = _task_checks(workflow, expect, self._workflow_tasks(workflow, run))
            if isinstance(task_checks, CaseFailure):
                unread.append(task_checks)
            else:
                checks.extend(task_checks)
        fields["expectations"] = tuple(checks)
        reasons = [check.describe_failure() for check in checks if not check.passed]
        culprit = None
        if workflow.status != "successful":
            culprit = self._culprit(nodes, run, depth)
        found = workflow_failure(
            workflow,
            culprit=culprit,
            status_held=status.passed,
            reasons=reasons,
            node_failures=node_failures,
        )
        return _verdict(found, unread, fields)

    def _check_node(
        self,
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
                    self._ensure_hosts(node_run.job, node_run.fields, node_run.read)
                found = self._check_node_job(node_run, expect, depth)
                checks = list(node_run.fields["expectations"])
        tagged = [check.model_copy(update={"node": node_id}) for check in checks]
        return tagged, None if found is None else in_node(found, node_id)

    def _check_node_job(
        self, node_run: NodeRun, expect: ExecutionChecks, depth: int
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
            return self._check_workflow(
                job, expect, {}, node_run.fields, node_run.read, depth=depth + 1
            )
        found = self._check(job, expect, node_run.fields, node_run.read)
        if found is not None and self._evidence:
            found = self._gather(job, found, node_run.read)
        return found

    def _culprit(
        self, nodes: Sequence[RunNode], run: WorkflowRun, depth: int
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
        found = self._check_node_job(node_run, Expectation(), depth)
        return None if found is None else in_node(found, node.label)

    def _unsaved_node_job(
        self, workflow: Job, expect: ExecutionChecks, run: WorkflowRun
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

    def _workflow_tasks(self, workflow: Job, run: WorkflowRun) -> tuple[FailedTask, ...] | None:
        """The failed tasks of every node job that failed (nested ones too); ``None``: unread."""
        tasks: list[FailedTask] = []
        try:
            for _, node, execution in run.node_jobs(workflow):
                if not node.failed or execution.kind not in JOB_ROUTES:
                    continue
                if execution.kind == WORKFLOW_JOB:
                    continue  # its own nodes follow
                node_run = run.run(node)
                found = self._job_tasks(node_run.job, node_run.fields, node_run.read)
                if found is None:
                    return None
                tasks.extend(found)
        except Exception:
            return None
        return tuple(tasks)

    def _ensure_hosts(self, job: Job, fields: dict[str, Any], read: JobRead) -> None:
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
                hosts, truncated = host_summaries(self._read_hosts(job))
        except Exception as exc:
            hosts, truncated, read.hosts_error = None, False, exc
        fields["hosts"], fields["hosts_truncated"] = hosts, truncated

    def _job_tasks(
        self, job: Job, fields: dict[str, Any], read: JobRead
    ) -> tuple[FailedTask, ...] | None:
        """The job's own failed tasks, read once; a successful job has none to read."""
        if job.status == "successful":
            read.tasks = ()
        else:
            # The job's summaries tell rescued failures from real ones.
            self._ensure_hosts(job, fields, read)
            hosts = fields["hosts"] if read.all_hosts is None else read.all_hosts
            read.tasks = self._failed_tasks(job, hosts)
        read.tasks_read = True
        return read.tasks

    def _host_checks(
        self,
        job: Job,
        expect: ExecutionChecks,
        fields: dict[str, Any],
        read: JobRead,
    ) -> list[ExpectationResult] | CaseFailure:
        """The host checks, or why the host summaries they need could not be read."""
        self._ensure_hosts(job, fields, read)
        if read.all_hosts is not None:
            return expect.check_hosts(read.all_hosts)
        hosts, hosts_error = fields["hosts"], read.hosts_error
        if hosts is not None and fields["hosts_truncated"]:
            hosts, hosts_error = self._beyond_the_cut(job, hosts, expect)
        if hosts is None and hosts_error is not None:
            return request_failure(hosts_error, message=f"host summaries unreadable: {hosts_error}")
        return expect.check_hosts(hosts or {})

    def _beyond_the_cut(
        self, job: Job, kept: dict[str, HostSummary], expect: ExecutionChecks
    ) -> tuple[dict[str, HostSummary] | None, Exception | None]:
        """``kept`` plus every host a check needs that the 500-host cut left out.

        Reads the hosts over each bound (and the named ones) with filtered
        host summary reads, so no host past the cut can hide a failed check.
        """
        found = dict(kept)
        try:
            for params in expect.host_filters(known=kept):
                found |= by_host(self._read_hosts(job, params))
        except Exception as exc:
            return None, exc
        return found, None

    def _update(self, job: Job, read: JobRead) -> Job | None:
        """The failed update the job names, as AWX has it now (``unknown`` if unreadable).

        Read once; ``None`` for a job that succeeded. A workflow node that ran
        an update itself is that update.
        """
        if not read.update_read:
            read.update, read.update_read = self._read_update(job), True
        return read.update

    def _read_update(self, job: Job) -> Job | None:
        if job.status == "successful":
            return None
        if job.kind in UPDATE_KINDS:
            return job
        update = responsible_update(job)
        if update is None:
            return None
        try:
            return self._reader.settled(self._reader.fetch(update))
        except Exception:
            return update

    def _gather(self, job: Job, failure: CaseFailure, read: JobRead) -> CaseFailure:
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
            tasks = () if source.status == "successful" else self._failed_tasks(source, None)
        log = read.log if source is job else None
        tail = self._tail(source, read) if log is None else tuple(log[-LOG_TAIL_LINES:])
        evidence = FailureEvidence.of(
            job,
            related=failure.evidence.related,
            log_tail=tail,
            failed_tasks=tasks,
            note=failure.evidence.note,
        )
        return failure.model_copy(update={"evidence": evidence})

    def _failed_tasks(
        self, job: Job, hosts: dict[str, HostSummary] | None
    ) -> tuple[FailedTask, ...] | None:
        """The job's failed tasks nothing handled (see :func:`unhandled`); ``None`` if unread.

        ``None`` too when there are none while AWX is still saving the events.
        """
        params = {"event__in": ",".join(FAILED_TASK_EVENTS)}
        try:
            events = list(self._read_events(job, params=params, follow=False))
        except Exception:
            return None
        tasks = unhandled(events, hosts)
        if not tasks and job.event_processing_finished is False:
            return None  # the failures may not be saved yet
        return tasks

    def _tail(self, job: Job, read: JobRead) -> tuple[str, ...] | None:
        """The last log lines, from the newest events only, read once; ``None`` if unreadable."""
        key = (job.kind, job.id)
        if key not in read.tails:
            try:
                read.tails[key] = tuple(self._read_tail(job, LOG_TAIL_LINES))
            except Exception:
                read.tails[key] = None
        return read.tails[key]

    def _changed_tasks(self, job: Job, read: JobRead | None) -> tuple[ChangedTask, ...] | None:
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
                for event in self._read_events(each, params=dict(_CHANGED_TASKS), follow=False)
                if event.changed
            )
            return tuple(islice(changed, CHANGED_TASKS_LIMIT))
        except Exception:
            return None


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


def _note(failures: Iterable[CaseFailure]) -> None:
    """Count ``failures`` toward the exit code of a run that is aborting."""
    for failure in failures:
        note_failure(failure)


def _collect_ref_sentinels(
    value: Any,
    by_kind: dict[str, list[dict[str, str] | None]],
    scope_for: Callable[[RefSentinel], dict[str, str] | None],
) -> None:
    """Walk every nested dict/list looking for ``!ref`` sentinels.

    Plain dicts and lists are recursed into so a ``!ref`` nested inside
    ``extra_vars`` is still discovered (the resolver does the same).
    Non-tagged dicts contribute no FK keys themselves — only declared
    top-level fields, handled separately, do.
    """
    if isinstance(value, RefSentinel):
        by_kind.setdefault(value.kind, []).append(scope_for(value))
        return
    if isinstance(value, dict):
        for sub in value.values():
            _collect_ref_sentinels(sub, by_kind, scope_for)
        return
    if isinstance(value, list):
        for item in value:
            _collect_ref_sentinels(item, by_kind, scope_for)


def _is_resolvable_fk_value(value: Any) -> bool:
    """A bare string, or a list containing at least one bare string."""
    if isinstance(value, str):
        return True
    return isinstance(value, list) and any(isinstance(item, str) for item in value)


def _merge_top_level(defaults: Case | None, case: Case) -> dict[str, Any]:
    """Defaults ⤥ case at the top-level launch keys (no deep merge here).

    Prefetch only cares about *which keys exist*, not their merged values,
    so a shallow merge captures FK-bearing keys from defaults that the
    case doesn't override.
    """
    out: dict[str, Any] = {}
    if defaults is not None:
        out.update(defaults.launch)
    out.update(case.launch)
    return out
