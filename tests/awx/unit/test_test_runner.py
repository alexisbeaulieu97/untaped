"""RunTestSuite: sequential + parallel runs, classification, ordering."""

from __future__ import annotations

import threading
import time
from typing import Any, cast

import pytest

from untaped.capabilities.awx.application.suites.ports import (
    FkPrefetcher,
    LaunchCheck,
    Launcher,
    Watcher,
)
from untaped.capabilities.awx.application.suites.resolver import ResolveCasePayload
from untaped.capabilities.awx.application.suites.runner import LOG_TAIL_LINES, RunTestSuite
from untaped.capabilities.awx.domain import Job, JobEvent
from untaped.capabilities.awx.domain.suite import Case, Suite
from untaped.capabilities.awx.errors import ActionResponseError, LaunchPromptError
from untaped.capabilities.awx.infrastructure import AwxResourceCatalog
from untaped.capabilities.awx.infrastructure.spec import AwxResourceSpec
from untaped.capabilities.awx.infrastructure.specs import JOB_TEMPLATE_SPEC
from untaped.capability_api import ConfigError


class StubFk:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def name_to_id(self, kind: str, name: str, *, scope: dict[str, str] | None = None) -> int:
        self.calls.append(("name_to_id", kind, name))
        return hash((kind, name)) & 0xFFFF

    def prefetch(self, plan: dict[str, list[dict[str, str] | None]]) -> None:
        self.calls.append(("prefetch", *plan.keys()))


class StubLauncher:
    """Records launch calls; returns either a Job or raises."""

    def __init__(self, behaviors: dict[str, Any]) -> None:
        # behaviors: case_name → {"job": Job}, or {"raises": Exception}
        self._behaviors = behaviors
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._counter = 1000
        self._next_lock = threading.Lock()

    def __call__(
        self,
        spec: AwxResourceSpec,
        *,
        name: str,
        action: str,
        scope: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Job:
        with self._lock:
            self.calls.append({"name": name, "action": action, "scope": scope, "payload": payload})
        # Resolve behavior by case (we encode it in extra_vars["case_name"]).
        case_name = (payload or {}).get("extra_vars", {}).get("case_name")
        beh = self._behaviors.get(case_name) if case_name else None
        if beh is None:
            beh = self._behaviors.get("__default__", {"job": _job(status="successful")})
        if "raises" in beh:
            raise beh["raises"]
        return beh["job"]


class StubCanceller:
    """Records cancel requests; refuses ``fail_ids`` like AWX answers 405."""

    def __init__(self, *, fail_ids: frozenset[int] = frozenset()) -> None:
        self.calls: list[int] = []
        self._fail_ids = fail_ids

    def __call__(self, *, kind: str, job_id: int) -> None:
        self.calls.append(job_id)
        if job_id in self._fail_ids:
            raise RuntimeError("405 Method not allowed")


class StubLogReader:
    """Returns ``lines`` as every job's stdout, or raises ``error``."""

    def __init__(self, lines: list[str], *, error: Exception | None = None) -> None:
        self.lines = lines
        self.error = error
        self.calls: list[int] = []

    def __call__(self, job: Job) -> list[str]:
        self.calls.append(job.id)
        if self.error is not None:
            raise self.error
        return self.lines


class StubEventReader:
    """Returns ``events`` for every job, or raises ``error``."""

    def __init__(self, events: list[JobEvent], *, error: Exception | None = None) -> None:
        self.events = events
        self.error = error
        self.calls: list[tuple[int, dict[str, str] | None, bool]] = []

    def __call__(
        self, job: Job, *, params: dict[str, str] | None = None, follow: bool = True
    ) -> list[JobEvent]:
        self.calls.append((job.id, params, follow))
        if self.error is not None:
            raise self.error
        return self.events


class StubWatcher:
    """Returns a final job per id."""

    def __init__(self, by_id: dict[int, Job] | None = None, default: Job | None = None) -> None:
        self._by_id = by_id or {}
        self._default = default or _job(status="successful")
        self.calls: list[tuple[int, float | None]] = []

    def __call__(self, job: Job, *, timeout: float | None = None) -> Job:
        self.calls.append((job.id, timeout))
        return self._by_id.get(job.id, self._default)


def _job(*, id_: int = 1000, status: str = "successful") -> Job:
    return Job.model_validate({"id": id_, "kind": "job", "name": "x", "status": status})


def _suite(name: str, cases: dict[str, dict[str, Any]]) -> Suite:
    return Suite(
        name=name,
        job_template="JT",
        cases={k: Case.model_validate({"launch": v}) for k, v in cases.items()},
    )


def _make_runner(
    *,
    fk: StubFk,
    launcher: StubLauncher,
    watcher: StubWatcher,
    default_org: str | None = None,
    canceller: StubCanceller | None = None,
    refresher: Any = None,
    log_reader: StubLogReader | None = None,
    event_reader: StubEventReader | None = None,
    preflight: LaunchCheck | None = None,
) -> RunTestSuite:
    resolver = ResolveCasePayload(
        fk, catalog=AwxResourceCatalog(), default_organization=default_org
    )
    jt_scope = {"organization": default_org} if default_org is not None else None
    return RunTestSuite(
        resolver=resolver,
        launcher=cast(Launcher, launcher),
        watcher=cast(Watcher, watcher),
        spec=JOB_TEMPLATE_SPEC,
        fk_prefetcher=cast(FkPrefetcher, fk),
        jt_scope=jt_scope,
        canceller=canceller,
        refresher=refresher,
        log_reader=log_reader or StubLogReader([]),
        event_reader=event_reader or StubEventReader([]),
        job_url=lambda job: f"https://aap.example.com/jobs/{job.id}",
        preflight=preflight,
    )


def test_parallel_interrupt_stops_watchers_and_cancels_queued_cases() -> None:
    """Ctrl-C sets the stop event, skips queued launches, and keeps launched jobs."""
    import os
    import signal

    from untaped.capabilities.awx.errors import WaitCancelledError

    stop = threading.Event()

    class BlockingWatcher:
        def __call__(self, job: Job, *, timeout: float | None = None) -> Job:
            if stop.wait(5):
                raise WaitCancelledError("wait interrupted")
            return job

    fk = StubFk()
    launcher = StubLauncher({"__default__": {"job": _job(id_=7, status="running")}})
    runner = RunTestSuite(
        resolver=ResolveCasePayload(fk, catalog=AwxResourceCatalog()),
        launcher=cast(Launcher, launcher),
        watcher=cast(Watcher, BlockingWatcher()),
        spec=JOB_TEMPLATE_SPEC,
        fk_prefetcher=cast(FkPrefetcher, fk),
        log_reader=StubLogReader([]),
        event_reader=StubEventReader([]),
        job_url=lambda job: None,
        stop=stop,
    )
    suite = _suite("s", {f"c{i}": {"extra_vars": {"case_name": f"c{i}"}} for i in range(6)})
    threading.Timer(0.2, os.kill, (os.getpid(), signal.SIGINT)).start()
    started = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        runner([suite], parallel=2)
    assert time.monotonic() - started < 2
    assert stop.is_set()
    assert len(launcher.calls) == 2
    assert [job.id for job in runner.launched] == [7, 7]


# ---- sequential runner --------------------------------------------------


@pytest.mark.parametrize(
    ("launched", "final", "result", "job_status"),
    [
        (_job(id_=1, status="pending"), _job(id_=1, status="successful"), "pass", "successful"),
        (_job(id_=1, status="pending"), _job(id_=1, status="failed"), "fail", "failed"),
        # a watch that ends before a terminal state is a timeout
        (_job(id_=1, status="pending"), _job(id_=1, status="running"), "timeout", "running"),
        (RuntimeError("boom"), None, "error", None),
    ],
)
def test_case_classification(
    launched: Job | Exception, final: Job | None, result: str, job_status: str | None
) -> None:
    behaviour = {"raises": launched} if isinstance(launched, Exception) else {"job": launched}
    runner = _make_runner(
        fk=StubFk(),
        launcher=StubLauncher({"a": behaviour}),
        watcher=StubWatcher(by_id={1: final} if final else None),
    )

    outcome = runner([_suite("s", {"a": {"extra_vars": {"case_name": "a"}}})], timeout=1.0)

    [row] = outcome.results
    assert (row.result, row.job_status) == (result, job_status)
    assert outcome.exit_code() == (0 if result == "pass" else 1)
    if result == "error":
        assert row.job_id is None
        assert "boom" in (row.failure_reason or "")


def test_all_name_lookups_complete_before_first_launch() -> None:
    """Workers must never call FkResolver — resolution finishes upfront."""
    sequence: list[str] = []
    fk = StubFk()

    original_name_to_id = fk.name_to_id

    def record_name_to_id(kind: str, name: str, *, scope: dict[str, str] | None = None) -> int:
        sequence.append("name_to_id")
        return original_name_to_id(kind, name, scope=scope)

    fk.name_to_id = record_name_to_id  # type: ignore[method-assign]
    launcher = RecordingLauncher(sequence, default_job=_job(status="successful"))
    watcher = StubWatcher()
    runner = _make_runner(fk=fk, launcher=launcher, watcher=watcher)
    suite = _suite(
        "s",
        {
            "a": {"inventory": "Inv-A", "extra_vars": {"case_name": "a"}},
            "b": {"inventory": "Inv-B", "extra_vars": {"case_name": "b"}},
        },
    )

    runner([suite])

    first_launch = sequence.index("launch")
    assert "name_to_id" not in sequence[first_launch:]


class RecordingLauncher:
    """Pushes ``"launch"`` into a shared sequence so ordering can be asserted."""

    def __init__(self, sequence: list[str], *, default_job: Job) -> None:
        self._sequence = sequence
        self._default = default_job

    def __call__(
        self,
        spec: AwxResourceSpec,
        *,
        name: str,
        action: str,
        scope: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Job:
        self._sequence.append("launch")
        return self._default


def test_case_filter_with_unmatched_names_raises() -> None:
    """Typos like ``--case smokee`` hard-fail before any launch, naming only the misses."""
    from untaped.capability_api import ConfigError

    launcher = StubLauncher({})
    runner = _make_runner(fk=StubFk(), launcher=launcher, watcher=StubWatcher())

    with pytest.raises(ConfigError, match="bogus") as exc_info:
        runner([_suite("s", {"keep": {}, "skip": {}})], case_filter={"keep", "bogus"})
    assert "keep" not in str(exc_info.value)
    assert launcher.calls == []


# ---- prefetch correctness ------------------------------------------------


def test_prefetch_does_not_walk_extra_vars() -> None:
    """The resolver's contract: opaque ``extra_vars`` content is not inspected."""
    fk = StubFk()
    launcher = StubLauncher({})
    watcher = StubWatcher()
    runner = _make_runner(fk=fk, launcher=launcher, watcher=watcher)
    suite = _suite(
        "s",
        {"a": {"extra_vars": {"inventory": "Web Inventory"}}},
    )

    runner([suite])

    prefetch_calls = [c for c in fk.calls if c[0] == "prefetch"]
    assert prefetch_calls == [("prefetch",)]  # empty plan — no Inventory entry


def test_prefetch_includes_defaults_top_level_fks() -> None:
    """A shared ``defaults.launch.inventory`` should warm the cache once."""
    fk = StubFk()
    launcher = StubLauncher({})
    watcher = StubWatcher()
    runner = _make_runner(fk=fk, launcher=launcher, watcher=watcher, default_org="org-a")
    defaults_case = Case.model_validate({"launch": {"inventory": "Web Inventory"}})
    suite = Suite(
        name="s",
        job_template="JT",
        defaults=defaults_case,
        cases={
            f"c{i}": Case.model_validate({"launch": {"extra_vars": {"i": i}}}) for i in range(3)
        },
    )

    runner([suite])

    prefetch_calls = [c for c in fk.calls if c[0] == "prefetch"]
    assert prefetch_calls == [("prefetch", "Inventory")]


def test_prefetch_uses_org_scope_for_org_scoped_fks() -> None:
    """Prefetch scope must match what the resolver will look up with."""
    captured: list[tuple[str, list[dict[str, str] | None]]] = []
    fk = StubFk()

    original_prefetch = fk.prefetch

    def record(plan: dict[str, list[dict[str, str] | None]]) -> None:
        captured.extend(plan.items())
        original_prefetch(plan)

    fk.prefetch = record  # type: ignore[method-assign]
    launcher = StubLauncher({})
    watcher = StubWatcher()
    runner = _make_runner(fk=fk, launcher=launcher, watcher=watcher, default_org="org-a")
    suite = _suite(
        "s",
        {"c": {"inventory": "Web Inventory"}},
    )

    runner([suite])

    assert captured == [("Inventory", [{"organization": "org-a"}])]


# ---- parallel runner ----------------------------------------------------


def test_parallel_results_preserve_input_order() -> None:
    fk = StubFk()

    def slow_then_fast(name: str) -> dict[str, Any]:
        if name == "slow":
            time.sleep(0.05)
        return {"job": _job(id_=hash(name) & 0xFF, status="successful")}

    behaviors: dict[str, Any] = {
        "slow": {"job": _job(id_=1, status="successful")},
        "fast": {"job": _job(id_=2, status="successful")},
    }
    launcher = SlowLauncher(behaviors, slow_cases={"slow"})
    watcher = StubWatcher(default=_job(status="successful"))
    runner = _make_runner(fk=fk, launcher=launcher, watcher=watcher)
    suite = _suite(
        "s",
        {
            "slow": {"extra_vars": {"case_name": "slow"}},
            "fast": {"extra_vars": {"case_name": "fast"}},
        },
    )

    outcome = runner([suite], parallel=2)

    # Case order in the result table follows declaration order, NOT completion.
    assert [r.case for r in outcome.results] == ["slow", "fast"]


class SlowLauncher(StubLauncher):
    def __init__(self, behaviors: dict[str, Any], *, slow_cases: set[str]) -> None:
        super().__init__(behaviors)
        self._slow = slow_cases

    def __call__(
        self,
        spec: AwxResourceSpec,
        *,
        name: str,
        action: str,
        scope: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Job:
        case_name = (payload or {}).get("extra_vars", {}).get("case_name")
        if case_name in self._slow:
            time.sleep(0.05)
        return super().__call__(spec, name=name, action=action, scope=scope, payload=payload)


@pytest.mark.parametrize("parallel", [1, 4])
def test_runner_returns_an_outcome_per_case(parallel: int) -> None:
    fk = StubFk()
    launcher = StubLauncher({})
    watcher = StubWatcher()
    runner = _make_runner(fk=fk, launcher=launcher, watcher=watcher)
    suite = _suite("s", {f"case_{i}": {"extra_vars": {"case_name": f"case_{i}"}} for i in range(5)})

    outcome = runner([suite], parallel=parallel)

    assert len(outcome.results) == 5
    assert {r.suite for r in outcome.results} == {"s"}


def test_interrupt_reports_ignored_field_executions_and_final_statuses() -> None:
    """Jobs created despite ignored_fields are launched too; finished ones are known.

    Ctrl-C cancels every unfinished one, except a job its timeout already cancelled.
    """

    class InterruptingWatcher:
        def __call__(self, job: Job, *, timeout: float | None = None) -> Job:
            if job.id == 8:
                raise KeyboardInterrupt
            return _job(id_=job.id, status="running" if job.id == 10 else "successful")

    fk = StubFk()
    launcher = StubLauncher(
        {
            "done": {"job": _job(id_=7, status="pending")},
            "ignored": {
                "raises": ActionResponseError(
                    "AWX ignored launch fields: limit", execution_id=9, execution_kind="job"
                )
            },
            "slow": {"job": _job(id_=10, status="pending")},
            "stuck": {"job": _job(id_=8, status="pending")},
        }
    )
    canceller = StubCanceller(fail_ids=frozenset({9}))
    runner = _make_runner(
        fk=fk, launcher=launcher, watcher=cast(Any, InterruptingWatcher()), canceller=canceller
    )
    suite = _suite(
        "s",
        {
            name: {"extra_vars": {"case_name": name}}
            for name in ("done", "ignored", "slow", "stuck")
        },
    )
    with pytest.raises(KeyboardInterrupt):
        runner([suite], timeout=60)
    assert {(job.id, job.is_terminal) for job in runner.known_executions()} == {
        (7, True),
        (9, False),
        (10, False),
        (8, False),
    }
    assert canceller.calls == [10, 9, 8]
    assert runner.cancelled == {("job", 10), ("job", 8)}


@pytest.mark.parametrize(
    ("canceller", "reason"),
    [
        (StubCanceller(), "cancel requested"),
        (StubCanceller(fail_ids=frozenset({5})), "cancel failed: 405 Method not allowed"),
        (None, "it keeps running"),
    ],
)
def test_timeout_cancels_the_job_and_says_so(canceller: StubCanceller | None, reason: str) -> None:
    runner = _make_runner(
        fk=StubFk(),
        launcher=StubLauncher({"__default__": {"job": _job(id_=5, status="pending")}}),
        watcher=StubWatcher(default=_job(id_=5, status="running")),
        canceller=canceller,
    )
    [result] = runner([_suite("s", {"a": {}})], timeout=60).results
    assert result.result == "timeout"
    assert result.failure_reason == f"still running after 60s; {reason}"
    if canceller is not None:
        assert canceller.calls == [5]


def test_timeout_reports_a_job_that_ended_before_its_cancel() -> None:
    runner = _make_runner(
        fk=StubFk(),
        launcher=StubLauncher({"__default__": {"job": _job(id_=5, status="pending")}}),
        watcher=StubWatcher(default=_job(id_=5, status="running")),
        canceller=StubCanceller(fail_ids=frozenset({5})),
        refresher=lambda job: _job(id_=job.id, status="successful"),
    )
    [result] = runner([_suite("s", {"a": {}})], timeout=60).results
    assert result.result == "timeout"
    assert result.job_status == "successful"
    assert result.failure_reason == (
        "still running after 60s; it ended (successful) before the cancel"
    )


def test_polling_error_cancels_the_job() -> None:
    class FailingWatcher:
        def __call__(self, job: Job, *, timeout: float | None = None) -> Job:
            raise RuntimeError("503 Service Unavailable")

    canceller = StubCanceller()
    runner = _make_runner(
        fk=StubFk(),
        launcher=StubLauncher({"__default__": {"job": _job(id_=5, status="pending")}}),
        watcher=cast(Any, FailingWatcher()),
        canceller=canceller,
    )
    [result] = runner([_suite("s", {"a": {}})]).results
    assert (result.result, result.failure_reason) == (
        "error",
        "503 Service Unavailable; cancel requested",
    )
    assert canceller.calls == [5]


# ---- expectations ------------------------------------------------------


def _expect_runner(
    final_status: str,
    log_reader: StubLogReader | None = None,
    event_reader: StubEventReader | None = None,
) -> tuple[RunTestSuite, StubWatcher]:
    watcher = StubWatcher(default=_job(id_=5, status=final_status))
    runner = _make_runner(
        fk=StubFk(),
        launcher=StubLauncher({"__default__": {"job": _job(id_=5, status="pending")}}),
        watcher=watcher,
        log_reader=log_reader,
        event_reader=event_reader,
    )
    return runner, watcher


def _case_suite(case: dict[str, Any], defaults: dict[str, Any] | None = None) -> Suite:
    return Suite(
        name="s",
        job_template="JT",
        defaults=Case.model_validate(defaults) if defaults is not None else None,
        cases={"c": Case.model_validate(case)},
    )


def test_a_failed_job_passes_when_failure_is_expected() -> None:
    reader = StubLogReader(["fatal: nope"])
    runner, _ = _expect_runner("failed", reader)
    [row] = runner([_case_suite({"expect": {"status": "failed"}})]).results
    assert row.result == "pass"
    assert [check.model_dump() for check in row.expectations] == [
        {"check": "status", "expected": "failed", "actual": "failed", "passed": True}
    ]
    assert (row.log_tail, row.failure_reason) == (None, None)
    assert reader.calls == []  # no log checks and nothing failed: no download
    assert row.job_url == "https://aap.example.com/jobs/5"


def test_failed_log_checks_fail_the_case_with_their_reasons_and_a_tail() -> None:
    lines = [f"line {i}" for i in range(60)] + ["fatal: boom"]
    runner, _ = _expect_runner("successful", StubLogReader(lines))
    suite = _case_suite(
        {"expect": {"log": {"contains": ["PLAY RECAP"]}}},
        defaults={"expect": {"log": {"not_contains": ["fatal:"]}}},
    )
    [row] = runner([suite]).results
    assert row.result == "fail"
    assert row.failure_reason == (
        "no log line contains 'PLAY RECAP'; log line contains 'fatal:': fatal: boom"
    )
    assert row.log_tail == tuple(lines[-LOG_TAIL_LINES:])


def test_a_status_mismatch_downloads_the_log_for_its_tail() -> None:
    runner, _ = _expect_runner("failed", StubLogReader(["a", "b"]))
    [row] = runner([_case_suite({})]).results
    assert (row.result, row.failure_reason, row.log_tail) == (
        "fail",
        "expected status successful, got failed",
        ("a", "b"),
    )


def test_an_unreadable_log_errors_only_when_log_checks_need_it() -> None:
    reader = StubLogReader([], error=RuntimeError("502 Bad Gateway"))
    runner, _ = _expect_runner("failed", reader)
    [row] = runner([_case_suite({})]).results
    assert (row.result, row.log_tail) == ("fail", None)

    runner, _ = _expect_runner("failed", reader)
    [row] = runner([_case_suite({"expect": {"log": {"contains": ["x"]}}})]).results
    assert (row.result, row.failure_reason) == (
        "error",
        "expected status successful, got failed; log fetch failed: 502 Bad Gateway",
    )
    assert [check.check for check in row.expectations] == ["status"]


def test_evidence_is_skipped_when_not_wanted() -> None:
    reader = StubLogReader(["boom"])
    events = StubEventReader([])
    runner = RunTestSuite(
        resolver=ResolveCasePayload(StubFk(), catalog=AwxResourceCatalog()),
        launcher=cast(Launcher, StubLauncher({})),
        watcher=cast(Watcher, StubWatcher(default=_job(status="failed"))),
        spec=JOB_TEMPLATE_SPEC,
        fk_prefetcher=cast(FkPrefetcher, StubFk()),
        log_reader=reader,
        event_reader=events,
        job_url=lambda job: None,
        evidence=False,
    )
    [row] = runner([_case_suite({})]).results
    assert (row.result, row.log_tail, row.failed_tasks) == ("fail", None, None)
    assert (reader.calls, events.calls) == ([], [])


def test_a_timed_out_case_keeps_its_log_tail() -> None:
    runner, _ = _expect_runner("running", StubLogReader(["TASK [slow]"]))
    [row] = runner([_case_suite({})], timeout=5).results
    assert (row.result, row.log_tail) == ("timeout", ("TASK [slow]",))


@pytest.mark.parametrize(
    ("cli", "case", "defaults", "expected"),
    [
        (None, None, None, 1800),
        (None, None, 600, 600),
        (None, 90, 600, 90),
        (30, 90, 600, 30),
    ],
)
def test_timeout_precedence(
    cli: float | None, case: float | None, defaults: float | None, expected: float
) -> None:
    runner, watcher = _expect_runner("successful")
    suite = _case_suite(
        {"timeout": case} if case else {},
        defaults={"timeout": defaults} if defaults else None,
    )
    runner([suite], timeout=cli, default_timeout=1800)
    assert watcher.calls == [(5, expected)]


def test_each_case_resolves_its_own_timeout() -> None:
    runner, watcher = _expect_runner("successful")
    suite = Suite(name="s", job_template="JT", cases={"slow": Case(timeout=90), "quick": Case()})
    runner([suite], default_timeout=1800)
    assert [timeout for _, timeout in watcher.calls] == [90, 1800]


# ---- failed tasks, scm branch, preflight ---------------------------------


def _event(event: str, *, failed: bool, host: str, msg: str) -> JobEvent:
    return JobEvent.model_validate(
        {
            "counter": 1,
            "event": event,
            "failed": failed,
            "host_name": host,
            "task": "Deploy",
            "event_data": {"res": {"msg": msg}},
        }
    )


def test_a_case_that_did_not_pass_lists_its_failed_tasks() -> None:
    events = StubEventReader(
        [
            _event("playbook_on_task_start", failed=True, host="web1", msg=""),
            _event("runner_on_failed", failed=True, host="web1", msg="boom"),
            # ``ignore_errors`` failures are not failures.
            _event("runner_on_failed", failed=False, host="web2", msg="ignored"),
            _event("runner_on_unreachable", failed=True, host="db1", msg="ssh timeout"),
        ]
    )
    runner, _ = _expect_runner("failed", event_reader=events)
    [row] = runner([_case_suite({})]).results
    assert row.failed_tasks is not None
    assert [(task.host, task.status, task.msg) for task in row.failed_tasks] == [
        ("web1", "failed", "boom"),
        ("db1", "unreachable", "ssh timeout"),
    ]
    assert events.calls == [
        (
            5,
            {"event__in": "runner_on_failed,runner_on_async_failed,runner_on_unreachable"},
            False,
        )
    ]


def test_passing_cases_skip_events_and_unreadable_events_leave_no_list() -> None:
    events = StubEventReader([])
    runner, _ = _expect_runner("successful", event_reader=events)
    [row] = runner([_case_suite({})]).results
    assert (row.failed_tasks, events.calls) == (None, [])

    runner, _ = _expect_runner("failed", event_reader=StubEventReader([], error=RuntimeError()))
    [row] = runner([_case_suite({})]).results
    assert (row.result, row.failed_tasks) == ("fail", None)


@pytest.mark.parametrize(("processed", "expected"), [(True, ()), (False, None)])
def test_no_failed_tasks_is_only_trusted_once_events_are_saved(
    processed: bool, expected: tuple[()] | None
) -> None:
    final = Job.model_validate(
        {"id": 5, "kind": "job", "status": "failed", "event_processing_finished": processed}
    )
    runner = _make_runner(
        fk=StubFk(),
        launcher=StubLauncher({"__default__": {"job": _job(id_=5, status="pending")}}),
        watcher=StubWatcher(default=final),
    )
    [row] = runner([_case_suite({})]).results
    assert row.failed_tasks == expected


def test_scm_branch_overrides_every_case_and_rows_report_what_ran() -> None:
    launcher = StubLauncher({"__default__": {"job": _job(id_=5, status="pending")}})
    final = Job.model_validate(
        {"id": 5, "kind": "job", "status": "successful", "scm_branch": "fix", "scm_revision": "c0"}
    )
    runner = _make_runner(fk=StubFk(), launcher=launcher, watcher=StubWatcher(default=final))
    suite = _case_suite({"launch": {"scm_branch": "main", "limit": "web"}})
    [row] = runner([suite], scm_branch="fix").results
    assert [call["payload"] for call in launcher.calls] == [{"scm_branch": "fix", "limit": "web"}]
    assert (row.scm_branch, row.scm_revision) == ("fix", "c0")


def test_preflight_failures_stop_the_run_before_any_launch() -> None:
    checked: list[tuple[str, dict[str, Any]]] = []

    def preflight(
        spec: object, *, name: str, scope: dict[str, str] | None, payload: dict[str, Any]
    ) -> None:
        checked.append((name, payload))
        if "limit" in payload:
            raise LaunchPromptError("does not prompt for limit")

    launcher = StubLauncher({})
    runner = _make_runner(
        fk=StubFk(), launcher=launcher, watcher=StubWatcher(), preflight=preflight
    )
    suite = _suite("s", {"ok": {}, "bad": {"limit": "web"}, "worse": {"limit": "db"}})
    with pytest.raises(ConfigError) as info:
        runner([suite])
    assert str(info.value) == (
        "preflight failed, nothing launched:\n"
        "  s/bad: does not prompt for limit\n"
        "  s/worse: does not prompt for limit"
    )
    assert launcher.calls == []
    assert [name for name, _ in checked] == ["JT", "JT", "JT"]
    assert info.value.category == "invalid"


def test_a_preflight_failure_carries_its_most_severe_category() -> None:
    def preflight(
        spec: object, *, name: str, scope: dict[str, str] | None, payload: dict[str, Any]
    ) -> None:
        if "limit" in payload:
            raise LaunchPromptError("does not prompt for limit")
        raise ConfigError("AWX rejected the token (HTTP 401)", category="auth", system="awx")

    runner = _make_runner(
        fk=StubFk(), launcher=StubLauncher({}), watcher=StubWatcher(), preflight=preflight
    )
    suite = _suite("s", {"bad": {"limit": "web"}, "denied": {}})
    with pytest.raises(ConfigError) as info:
        runner([suite])
    assert (info.value.category, info.value.system) == ("auth", "awx")
    assert info.value.exit_code == 4


def test_an_unknown_case_is_not_found() -> None:
    runner = _make_runner(fk=StubFk(), launcher=StubLauncher({}), watcher=StubWatcher())
    with pytest.raises(ConfigError, match="no case matched --case 'nope'") as info:
        runner([_suite("s", {"c": {}})], case_filter={"nope"})
    assert info.value.category == "not_found"


# ---- case selection and suite scope --------------------------------------


def test_case_filter_accepts_suite_slash_case() -> None:
    launcher = StubLauncher({})
    runner = _make_runner(fk=StubFk(), launcher=launcher, watcher=StubWatcher())
    one = _suite("one", {"smoke": {}, "full": {}})
    two = _suite("two", {"smoke": {}, "full": {}})
    outcome = runner([one, two], case_filter={"one/smoke", "full"})
    assert [(row.suite, row.case) for row in outcome.results] == [
        ("one", "smoke"),
        ("one", "full"),
        ("two", "full"),
    ]
    with pytest.raises(ConfigError, match="'three/smoke'"):
        runner([one, two], case_filter={"one/smoke", "three/smoke"})


def test_a_suite_organization_scopes_its_template() -> None:
    launcher = StubLauncher({})
    checked: list[dict[str, str] | None] = []

    def preflight(
        spec: object, *, name: str, scope: dict[str, str] | None, payload: dict[str, Any]
    ) -> None:
        checked.append(scope)

    runner = _make_runner(
        fk=StubFk(),
        launcher=launcher,
        watcher=StubWatcher(),
        default_org="Default",
        preflight=preflight,
    )
    ops = Suite(name="ops", job_template="JT", organization="Ops", cases={"c": Case()})
    plain = Suite(name="plain", job_template="JT", cases={"c": Case()})
    runner([ops, plain])
    expected = [{"organization": "Ops"}, {"organization": "Default"}]
    assert [call["scope"] for call in launcher.calls] == expected
    assert checked == expected
