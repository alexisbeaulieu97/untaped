"""RunTestSuite: load → plan → prefetch → resolve → launch+wait.

With a :class:`LaunchCheck`, every case's launch is checked before any job
runs. Each finished job is checked against its case's :class:`Expectation`
(status, then log checks read through a :class:`LogReader`). A case that does
not pass carries a :class:`CaseFailure`: the system responsible, decided by
the rules in :mod:`untaped.capabilities.awx.domain.case_failure` from the job,
its explanation and its failed tasks (from job events), and, with
``evidence``, what shows it: the failed tasks and log tail of the responsible
execution (the project or inventory update that failed first, else the job; a
tail reads only the newest events). Each failure counts toward the run's exit
code by its category. With a :class:`HostReader`, every case with a job
carries its hosts' summaries. With a :class:`Canceller`, every execution the
run stops watching before it ends (timeout, polling error, Ctrl-C) is
cancelled rather than left running. A failed preflight carries its most
severe problem's category and the system responsible.

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
from typing import Any

from untaped.capabilities.awx.application.abandon_jobs import AbandonJobs
from untaped.capabilities.awx.application.ports import Canceller
from untaped.capabilities.awx.application.suites.ports import (
    EventReader,
    FkPrefetcher,
    HostReader,
    LaunchCheck,
    Launcher,
    LogReader,
    TailReader,
    Watcher,
)
from untaped.capabilities.awx.application.suites.resolver import ResolveCasePayload
from untaped.capabilities.awx.domain import Job, ResourceSpec
from untaped.capabilities.awx.domain.case_failure import (
    FAILED_TASK_EVENTS,
    INVENTORY,
    SCM,
    CaseFailure,
    FailedTask,
    FailureEvidence,
    RelatedExecution,
    finished_failure,
    launch_system,
    request_failure,
    responsible_update,
    timeout_failure,
)
from untaped.capabilities.awx.domain.job import HostSummary, host_summaries
from untaped.capabilities.awx.domain.suite import (
    Case,
    CaseResult,
    Expectation,
    RefSentinel,
    Suite,
    SuiteRunOutcome,
)
from untaped.capabilities.awx.errors import ActionResponseError, AwxApiError
from untaped.capability_api import (
    ConfigError,
    UntapedError,
    attribution,
    bounded_map,
    most_severe,
    note_failure,
)

_LAUNCH_ACTION = "launch"
LOG_TAIL_LINES = 40
"""Log lines a case that did not pass carries in its result."""


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
        canceller: Canceller | None = None,
        refresher: Callable[[Job], Job] | None = None,
        preflight: LaunchCheck | None = None,
        evidence: bool = True,
        host_reader: HostReader | None = None,
    ) -> None:
        self._resolve = resolver
        self._launch = launcher
        self._watch = watcher
        self._spec = spec
        self._fk = fk_prefetcher
        self._jt_scope = jt_scope
        self._clock = clock
        self._stop = stop
        self._abandon = AbandonJobs(canceller, refresher=refresher)
        """Cancels (or, without ``canceller``, leaves) what the run stops watching."""
        self._read_log = log_reader
        self._read_events = event_reader
        self._read_tail = tail_reader
        self._job_url = job_url
        self._preflight = preflight
        self._evidence = evidence
        """Attach the evidence (failed tasks, log tail) to cases that did not pass."""
        self._read_hosts = host_reader
        """Reads each job's host summaries; ``None`` leaves ``hosts`` unread."""
        self._tasks: dict[tuple[str, int], tuple[FailedTask, ...] | None] = {}
        """Failed tasks per execution, read once for attribution and evidence."""
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
                    hits = {case_name, f"{suite.name}/{case_name}"} & case_filter
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
                **(attribution(worst) | {"system": launch_system(worst)}),
            )

    def _launch_and_wait(self, item: _ResolvedCase) -> CaseResult:
        """Launch, watch and check one case; a case that did not pass carries its ``failure``.

        Each failure counts toward the run's exit code by its category.
        """
        result = self._run_case(item)
        if result.failure is not None:
            note_failure(result.failure)
        return result

    def _run_case(self, item: _ResolvedCase) -> CaseResult:
        started_clock = self._clock()
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
            return CaseResult(
                suite=item.suite_name,
                case=item.case_name,
                result="error",
                job_id=exc.execution_id if isinstance(exc, ActionResponseError) else None,
                duration_s=self._clock() - started_clock,
                failure=request_failure(exc, launching=True),
            )
        log: list[str] | None = None
        failure: CaseFailure | None = None
        try:
            final = self._watch(job, timeout=item.timeout)
            self._finals[(final.kind, final.id)] = final
        except Exception as exc:
            final = job
            fields: dict[str, Any] = {"result": "error"}
            failure = request_failure(exc, summary=f"{exc}; {self._abandon(job)}")
        else:
            fields = {
                "job_status": final.status,
                "started_at": final.started,
                "finished_at": final.finished,
                "scm_branch": final.scm_branch,
                "scm_revision": final.scm_revision,
            }
            if final.is_terminal:
                checked, log, failure = self._check(final, item.expect)
                fields.update(checked)
            else:
                waited = f"still {final.status} after {item.timeout or 0:g}s"
                failure = timeout_failure(final, f"{waited}; {self._abandon(final)}")
                fields["result"] = "timeout"
                # A refused cancel re-reads the job: it may have ended meanwhile.
                final = self._finals[(final.kind, final.id)] = self._abandon.latest(final)
                fields.update(job_status=final.status, finished_at=final.finished)
            if self._read_hosts is not None:
                fields["hosts"], fields["hosts_truncated"] = self._hosts(final)
        if failure is not None and self._evidence:
            failure = failure.model_copy(update={"evidence": self._gather(final, failure, log)})
        return CaseResult(
            suite=item.suite_name,
            case=item.case_name,
            job_id=final.id,
            job_url=self._job_url(final),
            duration_s=self._clock() - started_clock,
            failure=failure,
            **fields,
        )

    def _check(
        self, job: Job, expect: Expectation
    ) -> tuple[dict[str, Any], list[str] | None, CaseFailure | None]:
        """Evaluate ``expect`` against a finished job: result fields, the log if read, a failure."""
        checks = [expect.check_status(job.status)]
        log: list[str] | None = None
        if expect.needs_log:
            try:
                log = self._read_log(job)
            except Exception as exc:
                failure = request_failure(exc, summary=f"log fetch failed: {exc}")
                return {"result": "error", "expectations": tuple(checks)}, None, failure
            checks.extend(expect.log.evaluate(log))
        reasons = [check.describe_failure() for check in checks if not check.passed]
        fields = {"result": "fail" if reasons else "pass", "expectations": tuple(checks)}
        if not reasons:
            return fields, log, None
        status_held = checks[0].passed
        tasks = None
        if not status_held and job.status != "successful":
            tasks = self._failed_tasks(responsible_update(job) or job)
        failure = finished_failure(
            job, reasons=reasons, status_held=status_held, failed_tasks=tasks
        )
        return fields, log, failure

    def _gather(self, job: Job, failure: CaseFailure, log: list[str] | None) -> FailureEvidence:
        """The evidence of ``failure``: from the failed update it names, else from the job."""
        update = responsible_update(job) if failure.system in (SCM, INVENTORY) else None
        source = update or job
        related = None
        if update is not None:
            related = RelatedExecution(
                kind=update.kind,
                id=update.id,
                name=update.name,
                status=update.status,
                url=self._job_url(update),
            )
            log = None  # the job's own log is not the evidence
        tasks = self._failed_tasks(source)
        tail = self._tail(source) if log is None else tuple(log[-LOG_TAIL_LINES:])
        return FailureEvidence.of(job, related=related, log_tail=tail, failed_tasks=tasks)

    def _failed_tasks(self, job: Job) -> tuple[FailedTask, ...] | None:
        """The job's failed tasks (``ignore_errors`` ones excluded); ``None`` if unreadable."""
        key = (job.kind, job.id)
        if key in self._tasks:
            return self._tasks[key]
        params = {"event__in": ",".join(FAILED_TASK_EVENTS)}
        try:
            events = list(self._read_events(job, params=params, follow=False))
        except Exception:
            return None
        tasks: tuple[FailedTask, ...] | None = tuple(
            FailedTask.from_event(event)
            for event in events
            # AWX also flags the play and task events above a failure.
            if event.failed and event.event in FAILED_TASK_EVENTS
        )
        if not tasks and job.event_processing_finished is False:
            tasks = None  # the failures may not be saved yet
        self._tasks[key] = tasks
        return tasks

    def _tail(self, job: Job) -> tuple[str, ...] | None:
        """The last log lines, from the newest events only; ``None`` if unreadable."""
        try:
            lines, _newest = self._read_tail(job, LOG_TAIL_LINES)
        except Exception:
            return None
        return tuple(lines)

    def _hosts(self, job: Job) -> tuple[dict[str, HostSummary] | None, bool]:
        """Every host's summary (up to 500) and whether more were cut; ``None`` if unreadable."""
        assert self._read_hosts is not None
        try:
            return host_summaries(self._read_hosts(job))
        except Exception:
            return None, False


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
