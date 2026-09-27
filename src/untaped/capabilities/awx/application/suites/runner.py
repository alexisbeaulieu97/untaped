"""RunTestSuite: load → plan → prefetch → resolve → launch+wait.

Each finished job is checked against its case's :class:`Expectation`
(status, then log checks read through a :class:`LogReader`); with
``log_tails``, a case that does not pass carries the tail of its log. With
a :class:`Canceller`, every execution the run stops watching before it ends
(timeout, polling error, Ctrl-C) is cancelled rather than left running.

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
from typing import Any

from untaped.capabilities.awx.application.suites.ports import (
    Canceller,
    FkPrefetcher,
    Launcher,
    LogReader,
    Watcher,
)
from untaped.capabilities.awx.application.suites.resolver import ResolveCasePayload
from untaped.capabilities.awx.domain import Job, ResourceSpec
from untaped.capabilities.awx.domain.suite import (
    Case,
    CaseResult,
    Expectation,
    RefSentinel,
    Suite,
    SuiteRunOutcome,
)
from untaped.capabilities.awx.errors import ActionResponseError
from untaped.capability_api import ConfigError, bounded_map

_LAUNCH_ACTION = "launch"
LOG_TAIL_LINES = 40
"""Log lines a case that did not pass carries in its result."""


@dataclass(frozen=True, slots=True)
class _ResolvedCase:
    suite_name: str
    case_name: str
    job_template: str
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
        job_url: Callable[[Job], str | None],
        jt_scope: dict[str, str] | None = None,
        clock: Callable[[], float] = time.monotonic,
        stop: threading.Event | None = None,
        canceller: Canceller | None = None,
        log_tails: bool = True,
    ) -> None:
        self._resolve = resolver
        self._launch = launcher
        self._watch = watcher
        self._spec = spec
        self._fk = fk_prefetcher
        self._jt_scope = jt_scope
        self._clock = clock
        self._stop = stop
        self._cancel = canceller
        """``None`` leaves executions the run stops watching still running."""
        self._read_log = log_reader
        self._job_url = job_url
        self._log_tails = log_tails
        """Attach ``log_tail`` to cases that did not pass (costs a download without log checks)."""
        self.launched: list[Job] = []
        """Executions submitted so far (for reporting after an interrupt)."""
        self.cancelled: set[tuple[str, int]] = set()
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
    ) -> SuiteRunOutcome:
        """Run the selected cases.

        A case waits ``timeout`` when given, else its own ``timeout:``, else its
        suite's ``defaults.timeout``, else ``default_timeout`` (``None``: forever).
        """
        plan = self._build_plan(list(suites), case_filter)
        self._fk.prefetch(self._prefetch_plan(plan))
        resolved = self._resolve_all(plan, timeout=timeout, default_timeout=default_timeout)

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
            self._cancel_unfinished()
            raise
        # Indexed by declaration order, so the report ignores completion order.
        return SuiteRunOutcome(results=[results[index] for index in range(len(resolved))])

    def known_executions(self) -> list[Job]:
        """Every submitted execution with its latest locally known status."""
        return [self._finals.get((job.kind, job.id), job) for job in self.launched]

    def _cancel_unfinished(self) -> None:
        for job in self.known_executions():
            if not job.is_terminal and (job.kind, job.id) not in self.cancelled:
                self._abandon(job)

    def _abandon(self, job: Job) -> str:
        """Cancel an execution the run stops watching; say what became of it."""
        if self._cancel is None:
            return "it keeps running"
        try:
            self._cancel(kind=job.kind, job_id=job.id)
        except Exception as exc:
            return f"cancel failed: {exc}"
        self.cancelled.add((job.kind, job.id))
        return "cancel requested"

    def _build_plan(
        self,
        suites: Sequence[Suite],
        case_filter: set[str] | None,
    ) -> list[tuple[Suite, str, Case]]:
        plan: list[tuple[Suite, str, Case]] = []
        for suite in suites:
            for case_name, case in suite.cases.items():
                if case_filter is not None and case_name not in case_filter:
                    continue
                plan.append((suite, case_name, case))
        if case_filter is not None:
            matched = {case_name for _, case_name, _ in plan}
            unmatched = sorted(case_filter - matched)
            if unmatched:
                raise ConfigError(
                    "no case matched --case " + ", ".join(repr(name) for name in unmatched)
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
            for field, value in merged.items():
                ref = fk_index.get(field)
                if ref is not None and _is_resolvable_fk_value(value):
                    assert ref.kind is not None
                    by_kind.setdefault(ref.kind, []).append(self._resolve.scope_for_fk_field(ref))
                _collect_ref_sentinels(value, by_kind, self._resolve.scope_for_ref)
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
            payload = self._resolve(self._spec, case, defaults=suite.defaults)
            expect = case.expect.over(defaults.expect)
            case_timeout = timeout or case.timeout or defaults.timeout or default_timeout
            out.append(
                _ResolvedCase(
                    suite.name, case_name, suite.job_template, payload, expect, case_timeout
                )
            )
        return out

    def _launch_and_wait(self, item: _ResolvedCase) -> CaseResult:
        started_clock = self._clock()
        try:
            job = self._launch(
                self._spec,
                name=item.job_template,
                action=_LAUNCH_ACTION,
                scope=self._jt_scope,
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
                failure_reason=str(exc),
            )
        log: list[str] | None = None
        try:
            final = self._watch(job, timeout=item.timeout)
            self._finals[(final.kind, final.id)] = final
        except Exception as exc:
            final = job
            fields: dict[str, Any] = {
                "result": "error",
                "failure_reason": f"{exc}; {self._abandon(job)}",
            }
        else:
            fields = {
                "job_status": final.status,
                "started_at": final.started,
                "finished_at": final.finished,
            }
            if final.is_terminal:
                checked, log = self._check(final, item.expect)
                fields.update(checked)
            else:
                waited = f"still {final.status} after {item.timeout or 0:g}s"
                fields.update(result="timeout", failure_reason=f"{waited}; {self._abandon(final)}")
        if fields["result"] != "pass" and self._log_tails:
            fields["log_tail"] = self._tail(final, log)
        return CaseResult(
            suite=item.suite_name,
            case=item.case_name,
            job_id=final.id,
            job_url=self._job_url(final),
            duration_s=self._clock() - started_clock,
            **fields,
        )

    def _check(self, job: Job, expect: Expectation) -> tuple[dict[str, Any], list[str] | None]:
        """Evaluate ``expect`` against a finished job: result fields, plus the log if read."""
        checks = [expect.check_status(job.status)]
        log: list[str] | None = None
        fetch_error: str | None = None
        if expect.needs_log:
            try:
                log = self._read_log(job)
            except Exception as exc:
                fetch_error = f"log fetch failed: {exc}"
            else:
                checks.extend(expect.log.evaluate(log))
        reasons = [check.describe_failure() for check in checks if not check.passed]
        if fetch_error is not None:
            reasons.append(fetch_error)
        result = "error" if fetch_error else "fail" if reasons else "pass"
        fields = {
            "result": result,
            "expectations": tuple(checks),
            "failure_reason": "; ".join(reasons) or None,
        }
        return fields, log

    def _tail(self, job: Job, log: list[str] | None) -> tuple[str, ...] | None:
        """The last lines of ``log`` (downloaded when ``None``); ``None`` if unreadable."""
        if log is None:
            try:
                log = self._read_log(job)
            except Exception:
                return None
        return tuple(log[-LOG_TAIL_LINES:])


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
