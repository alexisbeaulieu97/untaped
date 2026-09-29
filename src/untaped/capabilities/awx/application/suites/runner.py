"""RunTestSuite: load → plan → prefetch → resolve → launch+wait.

With a :class:`LaunchCheck`, every case's launch is checked before any job
runs. Each finished job is checked against its case's :class:`Expectation`
(status, then log checks read through a :class:`LogReader`, host bounds read
from its host summaries, completed with filtered reads past the 500-host cut,
failed tasks from its events). An ``idempotent`` case that passed is launched
again with the same payload (on the commit the first job ran), and the rerun
must succeed without changing anything. A case that does not pass carries a
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

A suite is bound to the template it launches in one step (``bind``: the
template it names, unless a caller substitutes another). A workflow case's
pending approvals are answered as the case says while it runs (or fail it
fast, cancelled, when it says nothing). Once it ended, its nodes are read
once for the row; a node job is read only when a check or the attribution
needs it: each node expectation is checked against its node's job by the same
code as a job case, the workflow's host summaries are its node jobs' summed,
and a failed workflow is attributed to the node that failed it, recursing
into nested workflows.

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
from untaped.capabilities.awx.application.suites.resolver import ResolveCasePayload
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
    failure_system,
    finished_failure,
    in_node,
    request_failure,
    responsible_update,
    tasks_unread,
    timeout_failure,
    unrescued,
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
    case_keys,
    idempotence,
    outranks_failure,
)
from untaped.capabilities.awx.domain.suite_baseline import saved_baselines
from untaped.capabilities.awx.domain.workflow_run import (
    APPROVAL,
    NEVER_RAN,
    WORKFLOW_JOB,
    RunNode,
    blamed_node,
    node_host_params,
    summed_host_records,
)
from untaped.capabilities.awx.errors import ActionResponseError, AwxApiError, PendingApprovalError
from untaped.capability_api import (
    ConfigError,
    ErrorCategory,
    UntapedError,
    attribution,
    bounded_map,
    most_severe,
    note_failure,
    q,
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
MAX_NESTING = 5
"""Nested workflows the attribution and the approvals follow, at most."""
_APPROVALS_HINT = "set `approvals: approve` or `approvals: deny` on the case (or in its defaults)"


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
        spec: ResourceSpec,
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
        canceller: Canceller | None = None,
        preflight: LaunchCheck | None = None,
        evidence: bool = True,
        hosts: bool = False,
        workflow_spec: ResourceSpec | None = None,
        bind: Callable[[Suite], TemplateBinding] | None = None,
        node_reader: NodeReader | None = None,
        approver: ApprovalDecider | None = None,
    ) -> None:
        self._resolve = resolver
        self._launch = launcher
        self._watch = watcher
        self._specs = {spec.kind: spec}
        if workflow_spec is not None:
            self._specs[workflow_spec.kind] = workflow_spec
        self._bind = bind or (lambda suite: suite.binding(jt_scope))
        """The template a suite's cases launch (a caller may bind another, e.g. a copy)."""
        self._read_nodes = node_reader
        self._decide = approver
        self._fk = fk_prefetcher
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
        self._hosts = hosts
        """Report every case's host summaries (a failed job's are read anyway)."""
        self.launched: list[Job] = []
        """Executions submitted so far (for reporting after an interrupt)."""
        self.cancelled = self._abandon.cancelled
        """``(kind, id)`` of executions whose cancel AWX accepted."""
        self._finals: dict[tuple[str, int], Job] = {}
        self._nodes: dict[int, list[RunNode]] = {}
        """A finished workflow job's nodes, by its id: read once."""
        self._node_runs: dict[int, _NodeRun] = {}
        """A finished node job, by its id: read once, with what checking it read."""
        self._decided: set[int] = set()
        """Approvals this run approved or denied."""
        self._denied: set[int] = set()

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
    ) -> SuiteRunOutcome:
        """Run the selected cases, and compare them with a baseline when given one.

        A case waits ``timeout`` when given, else its own ``timeout:``, else its
        suite's ``defaults.timeout``, else ``default_timeout`` (``None``: forever).
        ``scm_branch`` replaces every case's own. ``compare`` is a saved
        baseline; ``baseline`` is a ref every case runs on first, as the
        baseline, whose environment failures then count as this run's.
        """
        plan = self._build_plan(list(suites), case_filter)
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
                replace(item, payload={**item.payload, "scm_branch": scm_branch})
                for item in resolved
            ]
        self._check_launches(resolved)
        results: dict[int, CaseResult] = {}
        try:
            bounded_map(
                lambda index: self._launch_and_wait(resolved[index]),
                range(len(resolved)),
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
        return SuiteRunOutcome(results=[results[index] for index in range(len(resolved))])

    def known_executions(self) -> list[Job]:
        """Every submitted execution with its latest locally known status."""
        return [self._finals.get((job.kind, job.id), job) for job in self.launched]

    def _build_plan(
        self,
        suites: Sequence[Suite],
        case_filter: set[str] | None,
    ) -> list[tuple[Suite, str, Case]]:
        """Every case, or those ``case_filter`` names as ``case`` or ``suite/case``."""
        plan: list[tuple[Suite, str, Case]] = []
        matched: set[str] = set()
        for suite in suites:
            for case_name, case in suite.cases.items():
                if case_filter is not None:
                    hits = case_keys(suite.name, case_name) & case_filter
                    if not hits:
                        continue
                    matched |= hits
                plan.append((suite, case_name, case))
        if case_filter is not None:
            unmatched = sorted(case_filter - matched)
            if unmatched:
                raise ConfigError(
                    "no case matched --case " + ", ".join(repr(name) for name in unmatched),
                    category="not_found",
                )
        return plan

    def _prefetch_plan(
        self, plan: Sequence[tuple[Suite, str, Case]]
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
        for suite, _, case in plan:
            fk_index = ResolveCasePayload.fk_index_for(self._specs[suite.template_kind])
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
        plan: Sequence[tuple[Suite, str, Case]],
        *,
        timeout: float | None,
        default_timeout: float | None,
    ) -> list[_ResolvedCase]:
        out: list[_ResolvedCase] = []
        for suite, case_name, case in plan:
            defaults = suite.defaults or Case()
            binding = self._bind(suite)
            spec = self._specs[binding.kind]
            payload = self._resolve(
                spec, case, defaults=suite.defaults, organization=suite.organization
            )
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
                )
            )
        return out

    def _check_launches(self, resolved: Sequence[_ResolvedCase]) -> None:
        """Raise, listing every case AWX would reject or half-ignore, before any launch."""
        if self._preflight is None:
            return
        problems: list[str] = []
        errors: list[UntapedError] = []
        for item in resolved:
            check = partial(
                self._preflight,
                item.spec,
                name=item.template,
                scope=item.scope,
                payload=item.payload,
            )
            try:
                if item.workflow:
                    check(nodes=tuple(item.expect.nodes))  # the nodes the case checks
                else:
                    check()
            except (AwxApiError, ConfigError) as exc:
                problems.append(f"  {item.suite_name}/{item.case_name}: {exc}")
                errors.append(exc)
        if problems:
            worst = most_severe(errors)
            raise ConfigError(
                "\n".join(["preflight failed, nothing launched:", *problems]),
                **(attribution(worst) | {"system": failure_system(worst, launching=True)}),
            )

    def _launch_and_wait(self, item: _ResolvedCase) -> CaseResult:
        """Launch, watch and check one case (then its ``idempotent`` rerun).

        A case that did not pass carries its ``failure``. A run that is
        stopping (Ctrl-C) launches no rerun.
        """
        started_clock = self._clock()
        row, job = self._run_case(item, item.expect)
        stopping = self._stop is not None and self._stop.is_set()
        if item.expect.idempotent and row.failure is None and job is not None and not stopping:
            row = self._rerun(item, row, job)
        return row.model_copy(update={"duration_s": self._clock() - started_clock})

    def _rerun(self, item: _ResolvedCase, first: CaseResult, job: Job) -> CaseResult:
        """Launch a case that passed once more: the rerun must succeed and change nothing.

        The rerun runs the commit ``job`` ran (the one every job of a workflow
        ran) when the case names its ref. The
        row keeps the first job and adds ``rerun_job_id``; a rerun that failed
        is attributed as any job is, and one that changed something is the
        expectation's, with the tasks it changed as evidence.
        """
        if "scm_branch" in item.payload and (revision := self._revision(job)):
            item = replace(item, payload={**item.payload, "scm_branch": revision})
        rerun, rerun_job = self._run_case(item, _RERUN)
        check = idempotence(rerun)
        failure = rerun.failure
        if failure is not None and failure.system == EXPECTATION:
            # A rerun that succeeded fails only its ``changed`` check.
            changed = None
            if self._evidence and rerun_job is not None:
                changed = self._changed_tasks(rerun_job)
            evidence = failure.evidence.model_copy(update={"changed_tasks": changed})
            failure = failure.model_copy(
                update={"message": check.describe_failure(), "evidence": evidence}
            )
        elif failure is not None:
            what = "the rerun" if rerun.job_id is None else f"rerun job {rerun.job_id}"
            failure = failure.model_copy(update={"message": f"{what}: {failure.message}"})
        return first.model_copy(
            update={
                "result": rerun.result,
                "rerun_job_id": rerun.job_id,
                "expectations": (*first.expectations, check),
                "failure": failure,
            }
        )

    def _run_case(self, item: _ResolvedCase, expect: Expectation) -> tuple[CaseResult, Job | None]:
        """Launch the case's payload once, watch the job and check it against ``expect``.

        Returns the row and the job as last read (``None`` when the launch failed).
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
            # ``ignored_fields`` responses launched a job; keep its ID as evidence.
            if isinstance(exc, ActionResponseError) and exc.execution_id is not None:
                self.launched.append(
                    Job(
                        id=exc.execution_id,
                        kind=exc.execution_kind or "job",
                        status="unknown",
                    )
                )
            job_id = exc.execution_id if isinstance(exc, ActionResponseError) else None
            launch_failure = request_failure(exc, launching=True)
            return row(result="error", job_id=job_id, failure=launch_failure), None
        read = _Read(source=job)
        failure: CaseFailure | None = None
        try:
            final = self._watch_case(item, job)
            self._finals[(final.kind, final.id)] = final
        except Exception as exc:
            final = job
            fields: dict[str, Any] = {"result": "error"}
            failure = request_failure(exc, message=f"{exc}; {self._abandon(job)}")
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
        return result, final

    def _finish(
        self,
        item: _ResolvedCase,
        expect: Expectation,
        job: Job,
        fields: dict[str, Any],
        read: _Read,
    ) -> CaseFailure | None:
        """Check a finished job, or give up on one still running at the case's timeout.

        A workflow's host summaries (its node jobs', summed) are read only for a check.
        """
        hosts_error = None
        if expect.needs_hosts or (self._hosts and job.kind != WORKFLOW_JOB):
            fields["hosts"], fields["hosts_truncated"], hosts_error = self._host_summaries(job)
        if job.is_terminal:
            return self._check(job, expect, fields, read, hosts_error)
        waited = f"still {job.status} after {item.timeout or 0:g}s"
        failure = timeout_failure(job, f"{waited}; {self._abandon(job)}")
        fields["result"] = "timeout"
        # A refused cancel re-reads the job: it may have ended meanwhile.
        latest = self._finals[(job.kind, job.id)] = self._abandon.latest(job)
        fields.update(job_status=latest.status, finished_at=latest.finished)
        return failure

    def _check(
        self,
        job: Job,
        expect: ExecutionChecks,
        fields: dict[str, Any],
        read: _Read,
        hosts_error: Exception | None,
    ) -> CaseFailure | None:
        """Check a finished job against ``expect``; set ``result`` and ``expectations``.

        The job's own outcome is attributed first (a failed update, an error,
        a failed task), so a log, host summaries or events a check could not
        read never hide it: each becomes a note, or the case's error when
        nothing else failed.
        """
        if job.kind == WORKFLOW_JOB:
            return self._check_workflow(job, expect, fields, hosts_error, depth=0)
        status = expect.check_status(job.status)
        checks = [status]
        unread: list[CaseFailure] = []
        if expect.needs_log:
            try:
                read.log = self._read_log(job)
            except Exception as exc:
                unread.append(request_failure(exc, message=f"log fetch failed: {exc}"))
            else:
                checks.extend(expect.log.evaluate(read.log))
        if expect.needs_hosts:
            host_checks = self._host_checks(job, expect, fields, hosts_error)
            if isinstance(host_checks, CaseFailure):
                unread.append(host_checks)
            else:
                checks.extend(host_checks)
        update = self._update(job) if job.status != "successful" else None
        read.source = update or job
        if expect.failed_tasks and update is None:
            tasks = self._job_tasks(job, fields, read)
            if tasks is None:
                unread.append(tasks_unread(job))
            else:
                checks.extend(expect.check_failed_tasks(tasks))
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
        fields: dict[str, Any],
        hosts_error: Exception | None,
        *,
        depth: int,
    ) -> CaseFailure | None:
        """Check a finished workflow and each node the case names; set ``nodes`` too.

        The workflow's own checks read its node jobs (summed host summaries,
        the failed tasks of the nodes that failed); each node expectation is
        checked against its node's job, and a failed workflow is attributed
        to the node that failed it.
        """
        status = expect.check_status(workflow.status)
        checks = [status]
        unread: list[CaseFailure] = []
        nodes: list[RunNode] | None = None
        try:
            nodes = self._workflow_nodes(workflow)
        except Exception as exc:
            unread.append(request_failure(exc, message=f"workflow nodes unreadable: {exc}"))
        else:
            fields["nodes"] = tuple(node.result() for node in nodes)
        if expect.needs_log:
            message = "a workflow job has no log; check the log of a node under expect.nodes"
            unread.append(make_failure(SUITE, ErrorCategory.INVALID, message))
        if expect.needs_hosts:
            host_checks = self._host_checks(workflow, expect, fields, hosts_error)
            if isinstance(host_checks, CaseFailure):
                unread.append(host_checks)
            else:
                checks.extend(host_checks)
        node_failures: list[CaseFailure] = []
        node_expects = expect.nodes if isinstance(expect, Expectation) else {}
        for node_id, node_expect in node_expects.items():
            node_checks, node_failure = self._check_node(node_id, node_expect, nodes or [], depth)
            checks.extend(node_checks)
            if node_failure is None:
                continue
            if all(check.passed for check in node_checks):
                unread.append(node_failure)  # a check it could not make, not one that failed
            else:
                node_failures.append(node_failure)
        if expect.failed_tasks:
            tasks = self._workflow_tasks(nodes, depth) if nodes is not None else None
            if tasks is None:
                unread.append(tasks_unread(workflow))
            else:
                checks.extend(expect.check_failed_tasks(tasks))
        fields["expectations"] = tuple(checks)
        reasons = [check.describe_failure() for check in checks if not check.passed]
        culprit = None
        if workflow.status != "successful" and nodes is not None:
            culprit = self._culprit(nodes, depth)
        found = workflow_failure(
            workflow,
            culprit=culprit,
            status_held=status.passed,
            reasons=reasons,
            node_failures=node_failures,
        )
        return _verdict(found, unread, fields)

    def _check_node(
        self, node_id: str, expect: NodeExpectation, nodes: Sequence[RunNode], depth: int
    ) -> tuple[list[ExpectationResult], CaseFailure | None]:
        """A node expectation's checks (each naming the node), and the node's failure if any.

        A node that never ran has only its status; so do an approval and a
        management job. A node that ran a job is checked like a job case.
        """
        node = next((node for node in nodes if node.identifier == node_id), None)
        found: CaseFailure | None
        if node is None or node.job_id is None or node.kind not in JOB_ROUTES:
            checks = [expect.check_status(node.status if node is not None else NEVER_RAN)]
            found = _status_only(node, expect, checks[0])
        else:
            try:
                run = self._node_run(node)
            except Exception as exc:
                checks = [expect.check_status(node.status)]
                found = request_failure(exc, message=f"job {node.job_id} unreadable: {exc}")
            else:
                if expect.needs_hosts and "hosts" not in run.fields:
                    hosts = self._host_summaries(run.job)
                    run.fields["hosts"], run.fields["hosts_truncated"], run.hosts_error = hosts
                found = self._check_node_job(run, expect, depth)
                checks = list(run.fields["expectations"])
        tagged = [check.model_copy(update={"node": node_id}) for check in checks]
        return tagged, None if found is None else in_node(found, node_id)

    def _check_node_job(
        self, run: _NodeRun, expect: ExecutionChecks, depth: int
    ) -> CaseFailure | None:
        """``run``'s job (a nested workflow's too) checked against ``expect``, with evidence."""
        if run.job.kind == WORKFLOW_JOB:
            if depth + 1 >= MAX_NESTING:
                message = (
                    f"workflow job {run.job.id} ended {run.job.status}; workflows nested "
                    f"{MAX_NESTING} deep are not followed"
                )
                run.fields["expectations"] = (expect.check_status(run.job.status),)
                return make_failure(PLAYBOOK, ErrorCategory.FAILED, message)
            return self._check_workflow(
                run.job, expect, run.fields, run.hosts_error, depth=depth + 1
            )
        found = self._check(run.job, expect, run.fields, run.read, run.hosts_error)
        if found is not None and self._evidence:
            found = self._gather(run.job, found, run.read)
        return found

    def _culprit(self, nodes: Sequence[RunNode], depth: int) -> CaseFailure | None:
        """The failure of the node that failed the workflow, by the rules for its job."""
        node = blamed_node(nodes)
        if node is None or node.job_id is None:
            return None
        if node.kind == APPROVAL:
            denied = node.job_id in self._denied
            return in_node(approval_failure(node.template, node.job_id, denied=denied), node.label)
        if node.kind not in JOB_ROUTES:
            message = f"{node.kind} {node.job_id} ended {node.status}"
            return in_node(make_failure(CONTROLLER, ErrorCategory.UNAVAILABLE, message), node.label)
        try:
            run = self._node_run(node)
        except Exception as exc:
            message = f"job {node.job_id} unreadable: {exc}"
            return in_node(request_failure(exc, message=message), node.label)
        found = self._check_node_job(run, Expectation(), depth)
        return None if found is None else in_node(found, node.label)

    def _workflow_tasks(
        self, nodes: Sequence[RunNode], depth: int
    ) -> tuple[FailedTask, ...] | None:
        """The failed tasks of every node job that failed (nested ones too); ``None``: unread."""
        tasks: list[FailedTask] = []
        try:
            for node in nodes:
                if not node.failed or node.kind not in JOB_ROUTES:
                    continue
                run = self._node_run(node)
                if run.job.kind != WORKFLOW_JOB:
                    found = self._job_tasks(run.job, run.fields, run.read)
                elif depth + 1 < MAX_NESTING:
                    found = self._workflow_tasks(self._workflow_nodes(run.job), depth + 1)
                else:
                    found = None
                if found is None:
                    return None
                tasks.extend(found)
        except Exception:
            return None
        return tuple(tasks)

    def _workflow_nodes(self, workflow: Job) -> list[RunNode]:
        """A workflow job's nodes as they ran (a finished one's are read once)."""
        cached = self._nodes.get(workflow.id)
        if cached is not None:
            return cached
        if self._read_nodes is None:
            raise ConfigError("this run cannot read workflow nodes", category="usage")
        nodes = [RunNode.from_record(record) for record in self._read_nodes(workflow)]
        if workflow.is_terminal:
            self._nodes[workflow.id] = nodes
        return nodes

    def _node_run(self, node: RunNode) -> _NodeRun:
        """The finished job a node ran, read once (and what checking it reads)."""
        execution = node.execution
        assert execution is not None  # callers check the node ran
        run = self._node_runs.get(execution.id)
        if run is None:
            job = self._reader.settled(self._reader.fetch(execution))
            run = self._node_runs[execution.id] = _NodeRun(job, _Read(source=job), {})
        return run

    def _watch_case(self, item: _ResolvedCase, job: Job) -> Job:
        """Watch a case's job; a workflow's pending approvals are answered meanwhile."""
        if not item.workflow:
            return self._watch(job, timeout=item.timeout)
        answer = partial(self._answer_approvals, approvals=item.approvals, depth=0)
        return self._watch(job, timeout=item.timeout, on_state=answer)

    def _answer_approvals(self, workflow: Job, *, approvals: Approvals | None, depth: int) -> None:
        """Approve or deny each approval ``workflow`` (or a workflow nested in it) waits on.

        Raise :class:`PendingApprovalError` when the case gives no answer.
        """
        for node in self._workflow_nodes(workflow):
            execution = node.execution
            if execution is None or execution.is_terminal:
                continue
            if node.kind == WORKFLOW_JOB and depth + 1 < MAX_NESTING:
                self._answer_approvals(execution, approvals=approvals, depth=depth + 1)
            if node.kind != APPROVAL or node.status != "pending" or execution.id in self._decided:
                continue
            if approvals is None:
                raise PendingApprovalError(
                    f"node {node.label}: approval {q(node.template or execution.id)} is waiting, "
                    "and the case sets no approvals",
                    hint=_APPROVALS_HINT,
                )
            if self._decide is None:
                raise ConfigError("this run cannot answer approvals", category="usage")
            self._decide(execution.id, approve=approvals == "approve")
            self._decided.add(execution.id)
            if approvals == "deny":
                self._denied.add(execution.id)

    def _revision(self, job: Job) -> str | None:
        """The commit ``job`` ran; for a workflow, the one all its jobs ran (else ``None``)."""
        try:
            revisions = self._revisions(job)
        except Exception:
            return None
        return next(iter(revisions)) if len(revisions) == 1 else None

    def _revisions(self, job: Job) -> set[str | None]:
        if job.kind != WORKFLOW_JOB:
            return {job.scm_revision or None}
        found: set[str | None] = set()
        for node in self._workflow_nodes(job):
            if node.kind in ("job", WORKFLOW_JOB) and node.job_id is not None:
                found |= self._revisions(self._node_run(node).job)
        return found

    def _job_tasks(
        self, job: Job, fields: dict[str, Any], read: _Read
    ) -> tuple[FailedTask, ...] | None:
        """The job's own failed tasks, read once; a successful job has none to read."""
        if job.status == "successful":
            read.tasks = ()
        else:
            if "hosts" not in fields:
                # The job's summaries tell rescued failures from real ones.
                fields["hosts"], fields["hosts_truncated"], _ = self._host_summaries(job)
            read.tasks = self._failed_tasks(job, fields["hosts"])
        read.tasks_read = True
        return read.tasks

    def _host_checks(
        self,
        job: Job,
        expect: ExecutionChecks,
        fields: dict[str, Any],
        hosts_error: Exception | None,
    ) -> list[ExpectationResult] | CaseFailure:
        """The host checks, or why the host summaries they need could not be read."""
        hosts = fields["hosts"]
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
                found |= by_host(self._host_records(job, params))
        except Exception as exc:
            return None, exc
        return found, None

    def _update(self, job: Job) -> Job | None:
        """The failed update the job names, as AWX has it now (``unknown`` if unreadable).

        A workflow node that ran an update itself is that update.
        """
        if job.kind in UPDATE_KINDS:
            return job
        update = responsible_update(job)
        if update is None:
            return None
        try:
            return self._reader.settled(self._reader.fetch(update))
        except Exception:
            return update

    def _gather(self, job: Job, failure: CaseFailure, read: _Read) -> CaseFailure:
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
        tail = self._tail(source) if log is None else tuple(log[-LOG_TAIL_LINES:])
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
        """The job's failed tasks (``ignore_errors`` and rescued ones excluded); ``None`` if unread.

        ``None`` too when there are none while AWX is still saving the events.
        """
        params = {"event__in": ",".join(FAILED_TASK_EVENTS)}
        try:
            events = list(self._read_events(job, params=params, follow=False))
        except Exception:
            return None
        tasks = tuple(
            FailedTask.from_event(event)
            for event in events
            # AWX also flags the play and task events above a failure.
            if event.failed and event.event in FAILED_TASK_EVENTS
        )
        if not tasks and job.event_processing_finished is False:
            return None  # the failures may not be saved yet
        return unrescued(tasks, hosts)

    def _tail(self, job: Job) -> tuple[str, ...] | None:
        """The last log lines, from the newest events only; ``None`` if unreadable."""
        try:
            return tuple(self._read_tail(job, LOG_TAIL_LINES))
        except Exception:
            return None

    def _host_summaries(
        self, job: Job
    ) -> tuple[dict[str, HostSummary] | None, bool, Exception | None]:
        """Every host's summary (up to 500), whether more were cut, and why none were read."""
        try:
            hosts, truncated = host_summaries(self._host_records(job))
        except Exception as exc:
            return None, False, exc
        return hosts, truncated, None

    def _host_records(
        self, job: Job, params: Mapping[str, str] | None = None
    ) -> Iterable[Mapping[str, Any]]:
        """A job's host summary records; a workflow's are its node jobs', summed per host."""
        if job.kind != WORKFLOW_JOB:
            return self._read_hosts(job) if params is None else self._read_hosts(job, params)
        per_node = [
            self._host_records(execution, node_host_params(params))
            for node in self._workflow_nodes(job)
            if (execution := node.execution) is not None and execution.kind in ("job", WORKFLOW_JOB)
        ]
        return summed_host_records(per_node, params)

    def _changed_tasks(self, job: Job) -> tuple[ChangedTask, ...] | None:
        """The first tasks that changed a host, from one filtered events read (``None``: unread).

        A workflow's are those of its node jobs.
        """
        try:
            if job.kind == WORKFLOW_JOB:
                return self._node_changed_tasks(job)
            events = self._read_events(job, params=dict(_CHANGED_TASKS), follow=False)
            changed = (
                ChangedTask(host=event.host_name, task=event.task)
                for event in events
                if event.changed
            )
            return tuple(islice(changed, CHANGED_TASKS_LIMIT))
        except Exception:
            return None

    def _node_changed_tasks(self, workflow: Job) -> tuple[ChangedTask, ...] | None:
        tasks: list[ChangedTask] = []
        for node in self._workflow_nodes(workflow):
            execution = node.execution
            if execution is None or execution.kind not in ("job", WORKFLOW_JOB):
                continue
            found = self._changed_tasks(execution)
            if found is None:
                return None
            tasks.extend(found)
        return tuple(tasks[:CHANGED_TASKS_LIMIT])


@dataclass(slots=True)
class _Read:
    """What checking a case read, reused as its evidence."""

    source: Job
    """The responsible execution: the failed update the job names, else the job."""
    log: list[str] | None = None
    """The job's whole log, when a log expectation downloaded it."""
    tasks: tuple[FailedTask, ...] | None = None
    tasks_read: bool = False


@dataclass(slots=True)
class _NodeRun:
    """A workflow node's finished job, and what checking it read (kept for the next check)."""

    job: Job
    read: _Read
    fields: dict[str, Any]
    """The row fields checking it set (``hosts`` is reused)."""
    hosts_error: Exception | None = None


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


def _status_only(
    node: RunNode | None, expect: NodeExpectation, check: ExpectationResult
) -> CaseFailure | None:
    """The failure of a node with only a status: one that never ran, an approval, …"""
    checks_more = expect.needs_log or expect.needs_hosts or bool(expect.failed_tasks)
    if node is not None and node.job_id is not None and checks_more:
        message = f"a {node.kind} node has only a status to check"
        return make_failure(SUITE, ErrorCategory.INVALID, message)
    if check.passed:
        return None
    return make_failure(EXPECTATION, ErrorCategory.FAILED, check.describe_failure())


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
