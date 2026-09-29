"""RunTestSuite: load → plan → prefetch → resolve → launch+wait.

With a :class:`LaunchCheck`, every case's launch is checked before any job
runs. Each finished job is checked against its case's :class:`Expectation`
(status, then log checks read through a :class:`LogReader`, host bounds read
from its host summaries, failed tasks from its events). An ``idempotent`` case
that passed is launched again with the same payload, and the rerun must
succeed without changing anything. A case that does not pass carries a
:class:`CaseFailure`: the system responsible, decided by the rules in
:mod:`untaped.capabilities.awx.domain.case_failure` from the job, its
explanation and its failed tasks (from job events), and, with ``evidence``,
what shows it: the failed tasks and log tail of the responsible execution (the
project or inventory update that failed first, else the job; a tail reads only
the newest events), and the tasks a rerun changed. The caller counts the
failures toward the exit code (:meth:`SuiteRunOutcome.counted`), after any
comparison with a baseline. With ``hosts``, every case with a job carries its
hosts' summaries; a case whose checks need them reads them anyway. With a
:class:`Canceller`, every execution the run stops watching before it ends
(timeout, polling error, Ctrl-C) is cancelled rather than left running. A
failed preflight carries its most severe problem's category and the system
responsible.

Resolution finishes in the main thread before any worker is spawned so
the launch+wait pool only sees fully-baked, immutable launch dicts —
keeps workers free of FK lookups entirely. (:class:`FkResolver`'s caches
are thread-safe per issue #208, but skipping the lock dance is still
cleaner than racing workers through it.)
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from functools import partial
from itertools import islice
from typing import Any

from untaped.capabilities.awx.application.abandon_jobs import AbandonJobs
from untaped.capabilities.awx.application.ports import Canceller
from untaped.capabilities.awx.application.suites.ports import (
    EventReader,
    FkPrefetcher,
    HostReader,
    JobReader,
    LaunchCheck,
    Launcher,
    LogReader,
    TailReader,
    Watcher,
)
from untaped.capabilities.awx.application.suites.resolver import ResolveCasePayload
from untaped.capabilities.awx.domain import Job, ResourceSpec
from untaped.capabilities.awx.domain.case_failure import (
    EXPECTATION,
    FAILED_TASK_EVENTS,
    CaseFailure,
    ChangedTask,
    FailedTask,
    FailureEvidence,
    failure_system,
    finished_failure,
    request_failure,
    responsible_update,
    tasks_unread,
    timeout_failure,
    unrescued,
)
from untaped.capabilities.awx.domain.job import HostSummary, host_summaries
from untaped.capabilities.awx.domain.suite import (
    Case,
    CaseResult,
    Expectation,
    RefSentinel,
    Suite,
    SuiteRunOutcome,
    case_keys,
    total_changed,
)
from untaped.capabilities.awx.errors import ActionResponseError, AwxApiError
from untaped.capability_api import (
    ConfigError,
    UntapedError,
    attribution,
    bounded_map,
    most_severe,
    plural,
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
    job_template: str
    scope: dict[str, str] | None
    payload: dict[str, Any]
    expect: Expectation
    timeout: float | None


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
    ) -> None:
        self._resolve = resolver
        self._launch = launcher
        self._watch = watcher
        self._spec = spec
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
    ) -> SuiteRunOutcome:
        """Run the selected cases.

        A case waits ``timeout`` when given, else its own ``timeout:``, else its
        suite's ``defaults.timeout``, else ``default_timeout`` (``None``: forever).
        ``scm_branch`` replaces every case's own.
        """
        plan = self._build_plan(list(suites), case_filter)
        self._fk.prefetch(self._prefetch_plan(plan))
        resolved = self._resolve_all(
            plan, timeout=timeout, default_timeout=default_timeout, scm_branch=scm_branch
        )
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
        fk_index = ResolveCasePayload.fk_index_for(self._spec)
        for suite, _, case in plan:
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
        scm_branch: str | None,
    ) -> list[_ResolvedCase]:
        out: list[_ResolvedCase] = []
        for suite, case_name, case in plan:
            defaults = suite.defaults or Case()
            payload = self._resolve(
                self._spec, case, defaults=suite.defaults, organization=suite.organization
            )
            if scm_branch is not None:
                payload["scm_branch"] = scm_branch
            expect = case.expect.over(defaults.expect)
            case_timeout = timeout or case.timeout or defaults.timeout or default_timeout
            out.append(
                _ResolvedCase(
                    suite.name,
                    case_name,
                    suite.job_template,
                    suite.scope(self._jt_scope),
                    payload,
                    expect,
                    case_timeout,
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
            try:
                self._preflight(
                    self._spec, name=item.job_template, scope=item.scope, payload=item.payload
                )
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

        A case that did not pass carries its ``failure``.
        """
        started_clock = self._clock()
        run = self._run_case(item, item.expect)
        if item.expect.idempotent and run.failure is None:
            run = self._rerun(item, run)
        return CaseResult(
            suite=item.suite_name,
            case=item.case_name,
            duration_s=self._clock() - started_clock,
            failure=run.failure,
            **run.fields,
        )

    def _rerun(self, item: _ResolvedCase, first: _Run) -> _Run:
        """Launch a case that passed once more: the rerun must succeed and change nothing.

        The row keeps the first job and adds ``rerun_job_id``; a rerun that
        failed is attributed as any job is, and one that changed something is
        the expectation's, with the tasks it changed as evidence.
        """
        rerun = self._run_case(item, _RERUN)
        rerun_id = rerun.fields["job_id"]
        hosts = rerun.fields.get("hosts")
        changed = None if hosts is None else total_changed(hosts)
        check = Expectation.check_rerun(rerun.fields.get("job_status"), changed)
        fields = first.fields | {
            "result": rerun.fields["result"],
            "rerun_job_id": rerun_id,
            "expectations": (*first.fields["expectations"], check),
        }
        failure = rerun.failure
        if failure is None:
            return _Run(fields, None)
        if failure.system == EXPECTATION and changed is not None and rerun.job is not None:
            message = f"not idempotent: rerun job {rerun_id} changed {plural(changed, 'task')}"
            evidence = failure.evidence
            if self._evidence:
                evidence = evidence.model_copy(
                    update={"changed_tasks": self._changed_tasks(rerun.job)}
                )
            failure = failure.model_copy(update={"message": message, "evidence": evidence})
        else:
            rerun_job = "the rerun" if rerun_id is None else f"rerun job {rerun_id}"
            failure = failure.model_copy(update={"message": f"{rerun_job}: {failure.message}"})
        return _Run(fields, failure)

    def _run_case(self, item: _ResolvedCase, expect: Expectation) -> _Run:
        """Launch the case's payload once, watch the job and check it against ``expect``."""
        try:
            job = self._launch(
                self._spec,
                name=item.job_template,
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
            return _Run({"result": "error", "job_id": job_id}, request_failure(exc, launching=True))
        read = _Read(source=job)
        failure: CaseFailure | None = None
        try:
            final = self._watch(job, timeout=item.timeout)
            self._finals[(final.kind, final.id)] = final
        except Exception as exc:
            final = job
            fields: dict[str, Any] = {"result": "error"}
            failure = request_failure(exc, message=f"{exc}; {self._abandon(job)}")
        else:
            if final.is_terminal:
                # Failed tasks and host summaries are only complete once saved.
                final = self._finals[(final.kind, final.id)] = self._reader.settled(final)
            fields = {
                "job_status": final.status,
                "started_at": final.started,
                "finished_at": final.finished,
                "scm_branch": final.scm_branch,
                "scm_revision": final.scm_revision,
            }
            if self._hosts or expect.needs_hosts:
                fields["hosts"], fields["hosts_truncated"] = self._host_summaries(final, read)
            if final.is_terminal:
                failure = self._check(final, expect, fields, read)
            else:
                waited = f"still {final.status} after {item.timeout or 0:g}s"
                failure = timeout_failure(final, f"{waited}; {self._abandon(final)}")
                fields["result"] = "timeout"
                # A refused cancel re-reads the job: it may have ended meanwhile.
                final = self._finals[(final.kind, final.id)] = self._abandon.latest(final)
                fields.update(job_status=final.status, finished_at=final.finished)
        if failure is not None and self._evidence:
            failure = self._gather(final, failure, read)
        fields.update(job_id=final.id, job_url=self._job_url(final))
        return _Run(fields, failure, final)

    def _check(
        self, job: Job, expect: Expectation, fields: dict[str, Any], read: _Read
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
        if expect.needs_log:
            try:
                read.log = self._read_log(job)
            except Exception as exc:
                unread.append(request_failure(exc, message=f"log fetch failed: {exc}"))
            else:
                checks.extend(expect.log.evaluate(read.log))
        hosts = fields.get("hosts")
        if hosts is not None:
            checks.extend(expect.check_hosts(hosts))
        elif read.hosts_error is not None and expect.needs_hosts:
            error = read.hosts_error
            unread.append(request_failure(error, message=f"host summaries unreadable: {error}"))
        update = self._update(job) if job.status != "successful" else None
        read.source = update or job
        if expect.failed_tasks and update is None:
            read.tasks, read.tasks_read = self._failed_tasks(job, hosts), True
            if read.tasks is None:
                unread.append(tasks_unread(job))
            else:
                checks.extend(expect.check_failed_tasks(read.tasks))
        fields["expectations"] = tuple(checks)
        reasons = [check.describe_failure() for check in checks if not check.passed]
        if job.status != "successful" and not read.tasks_read and (update is not None or reasons):
            if update is None and "hosts" not in fields:
                # The job's summaries tell rescued failures from real ones.
                fields["hosts"], fields["hosts_truncated"] = self._host_summaries(job, read)
            hosts = fields["hosts"] if update is None else None
            read.tasks = self._failed_tasks(read.source, hosts)
            read.tasks_read = True
        failure = finished_failure(
            job,
            update=update,
            update_url=self._job_url(update) if update is not None else None,
            status_held=status.passed,
            reasons=reasons,
            failed_tasks=read.tasks,
        )
        if failure is None and unread:
            fields["result"] = "error"
            failure, *unread = unread
        else:
            fields["result"] = "pass" if failure is None else "fail"
        if failure is not None and unread:
            note = "; ".join(problem.message for problem in unread)
            failure = failure.model_copy(
                update={"evidence": failure.evidence.model_copy(update={"note": note})}
            )
        return failure

    def _update(self, job: Job) -> Job | None:
        """The failed update the job names, as AWX has it now (``unknown`` if unreadable)."""
        update = responsible_update(job)
        if update is None:
            return None
        try:
            return self._reader.settled(self._reader.fetch(update))
        except Exception:
            return update

    def _gather(self, job: Job, failure: CaseFailure, read: _Read) -> CaseFailure:
        """``failure`` with its evidence, from the responsible execution: ``related`` or the job."""
        source = read.source if failure.evidence.related is not None else job
        tasks = read.tasks if read.tasks_read else self._failed_tasks(source, None)
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

    def _host_summaries(self, job: Job, read: _Read) -> tuple[dict[str, HostSummary] | None, bool]:
        """Every host's summary (up to 500) and whether more were cut; ``None`` if unreadable."""
        try:
            return host_summaries(self._read_hosts(job))
        except Exception as exc:
            read.hosts_error = exc
            return None, False

    def _changed_tasks(self, job: Job) -> tuple[ChangedTask, ...] | None:
        """The first tasks that changed a host, from one filtered events read (``None``: unread)."""
        try:
            events = self._read_events(job, params=dict(_CHANGED_TASKS), follow=False)
            changed = (
                ChangedTask(host=event.host_name, task=event.task)
                for event in events
                if event.changed
            )
            return tuple(islice(changed, CHANGED_TASKS_LIMIT))
        except Exception:
            return None


@dataclass(slots=True)
class _Read:
    """What checking a case read, reused as its evidence."""

    source: Job
    """The responsible execution: the failed update the job names, else the job."""
    log: list[str] | None = None
    """The job's whole log, when a log expectation downloaded it."""
    tasks: tuple[FailedTask, ...] | None = None
    tasks_read: bool = False
    hosts_error: Exception | None = None
    """Why the host summaries could not be read."""


@dataclass(slots=True)
class _Run:
    """One launch of a case: the row's fields, its failure, and the job as last read."""

    fields: dict[str, Any]
    failure: CaseFailure | None
    job: Job | None = None
    """``None`` when the launch failed."""


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
