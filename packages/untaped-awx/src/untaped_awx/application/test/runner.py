"""RunTestSuite: load → plan → prefetch → resolve → launch+wait.

Resolution finishes in the main thread before any worker is spawned —
:class:`FkResolver`'s caches aren't thread-safe, so the launch+wait pool
only sees fully-baked, immutable launch dicts.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any, Protocol

from untaped_awx.application.test.ports import Launcher, Watcher
from untaped_awx.application.test.resolver import ResolveCasePayload
from untaped_awx.domain import Job
from untaped_awx.domain.test_suite import (
    Case,
    CaseResult,
    CaseStatus,
    RefSentinel,
    TestRunOutcome,
    TestSuite,
)
from untaped_awx.errors import AwxApiError

if TYPE_CHECKING:
    from untaped_awx.infrastructure.spec import AwxResourceSpec

_LAUNCH_ACTION = "launch"


class FkPrefetcher(Protocol):
    """Subset of :class:`FkResolver` the runner uses to warm caches upfront."""

    def prefetch(self, plan: dict[str, list[dict[str, str] | None]]) -> None: ...


class _ResolvedCase:
    __slots__ = ("case_name", "job_template", "payload", "suite_name")

    def __init__(
        self,
        suite_name: str,
        case_name: str,
        job_template: str,
        payload: dict[str, Any],
    ) -> None:
        self.suite_name = suite_name
        self.case_name = case_name
        self.job_template = job_template
        self.payload = payload


class RunTestSuite:
    def __init__(
        self,
        *,
        resolver: ResolveCasePayload,
        launcher: Launcher,
        watcher: Watcher,
        spec: AwxResourceSpec,
        fk_prefetcher: FkPrefetcher,
        jt_scope: dict[str, str] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._resolve = resolver
        self._launch = launcher
        self._watch = watcher
        self._spec = spec
        self._fk = fk_prefetcher
        self._jt_scope = jt_scope
        self._clock = clock

    def __call__(
        self,
        suites: Iterable[TestSuite],
        *,
        case_filter: set[str] | None = None,
        parallel: int = 1,
        timeout: float | None = None,
    ) -> TestRunOutcome:
        plan = self._build_plan(list(suites), case_filter)
        self._fk.prefetch(self._prefetch_plan(plan))
        resolved = self._resolve_all(plan)

        if parallel <= 1:
            results = [self._launch_and_wait(item, timeout) for item in resolved]
        else:
            with ThreadPoolExecutor(max_workers=parallel) as pool:
                # ``map`` materialises results in submission order, so the
                # report follows declaration order regardless of completion.
                results = list(
                    pool.map(lambda item: self._launch_and_wait(item, timeout), resolved)
                )
        return TestRunOutcome(results=results)

    def _build_plan(
        self,
        suites: Sequence[TestSuite],
        case_filter: set[str] | None,
    ) -> list[tuple[TestSuite, str, Case]]:
        plan: list[tuple[TestSuite, str, Case]] = []
        for suite in suites:
            for case_name, case in suite.cases.items():
                if case_filter is not None and case_name not in case_filter:
                    continue
                plan.append((suite, case_name, case))
        if case_filter is not None:
            matched = {case_name for _, case_name, _ in plan}
            unmatched = sorted(case_filter - matched)
            if unmatched:
                raise AwxApiError(
                    "no case matched --case " + ", ".join(repr(name) for name in unmatched)
                )
        return plan

    def _prefetch_plan(
        self, plan: Sequence[tuple[TestSuite, str, Case]]
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
        fk_index = {
            ref.field: ref
            for ref in (*self._spec.fk_refs, *self._spec.launch_fk_refs)
            if not ref.polymorphic and ref.kind is not None
        }
        for suite, _, case in plan:
            merged = _merge_top_level(suite.defaults, case)
            for field, value in merged.items():
                ref = fk_index.get(field)
                if ref is not None and _is_resolvable_fk_value(value):
                    assert ref.kind is not None
                    by_kind.setdefault(ref.kind, []).append(self._resolve.scope_for_fk_field(ref))
                _collect_ref_sentinels(value, by_kind, self._resolve.scope_for_ref)
        return by_kind

    def _resolve_all(self, plan: Sequence[tuple[TestSuite, str, Case]]) -> list[_ResolvedCase]:
        out: list[_ResolvedCase] = []
        for suite, case_name, case in plan:
            payload = self._resolve(self._spec, case, defaults=suite.defaults)
            out.append(_ResolvedCase(suite.name, case_name, suite.job_template, payload))
        return out

    def _launch_and_wait(self, item: _ResolvedCase, timeout: float | None) -> CaseResult:
        started_clock = self._clock()
        try:
            job = self._launch(
                self._spec,
                name=item.job_template,
                action=_LAUNCH_ACTION,
                scope=self._jt_scope,
                payload=item.payload,
            )
        except Exception as exc:
            return CaseResult(
                suite=item.suite_name,
                case=item.case_name,
                result="error",
                duration_s=self._clock() - started_clock,
                failure_reason=str(exc),
            )
        try:
            final = self._watch(job, timeout=timeout)
        except Exception as exc:
            return CaseResult(
                suite=item.suite_name,
                case=item.case_name,
                result="error",
                job_id=job.id,
                duration_s=self._clock() - started_clock,
                failure_reason=str(exc),
            )
        return _classify(item.suite_name, item.case_name, final, self._clock() - started_clock)


def _classify(suite_name: str, case_name: str, job: Job, duration_s: float) -> CaseResult:
    if not job.is_terminal:
        return CaseResult(
            suite=suite_name,
            case=case_name,
            result="timeout",
            job_status=job.status,
            job_id=job.id,
            duration_s=duration_s,
            started_at=job.started,
            finished_at=job.finished,
        )
    result: CaseStatus = "pass" if job.status == "successful" else "fail"
    return CaseResult(
        suite=suite_name,
        case=case_name,
        result=result,
        job_status=job.status,
        job_id=job.id,
        duration_s=duration_s,
        started_at=job.started,
        finished_at=job.finished,
    )


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
