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
from untaped.capabilities.awx.domain.case_failure import FailureEvidence
from untaped.capabilities.awx.domain.suite import Baseline, Case, Suite, SuiteRunOutcome
from untaped.capabilities.awx.errors import ActionResponseError, LaunchPromptError
from untaped.capabilities.awx.infrastructure import AwxResourceCatalog
from untaped.capabilities.awx.infrastructure.spec import AwxResourceSpec
from untaped.capability_api import ConfigError, HttpTransportError, note_failure
from untaped.diagnostics import diagnostics_scope, failure_exit_code


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
    """Returns ``lines`` as every job's stdout (whole or its tail), or raises ``error``."""

    def __init__(self, lines: list[str], *, error: Exception | None = None) -> None:
        self.lines = lines
        self.error = error
        self.calls: list[int] = []
        self.tail_calls: list[tuple[str, int]] = []

    def __call__(self, job: Job) -> list[str]:
        self.calls.append(job.id)
        if self.error is not None:
            raise self.error
        return self.lines

    def tail(self, job: Job, lines: int) -> list[str]:
        self.tail_calls.append((job.kind, job.id))
        if self.error is not None:
            raise self.error
        return self.lines[-lines:]


class StubJobReader:
    """Re-reads executions: ``records`` by ``(kind, id)``, else the job as given.

    ``settled`` serves ``unsaved`` reads with ``event_processing_finished`` false first.
    """

    def __init__(
        self, records: dict[tuple[str, int], Job] | None = None, *, unsaved: int = 0
    ) -> None:
        self.records = records or {}
        self.unsaved = unsaved
        self.fetched: list[tuple[str, int]] = []
        self.settled_ids: list[int] = []

    def fetch(self, job: Job) -> Job:
        self.fetched.append((job.kind, job.id))
        return self.records.get((job.kind, job.id), job)

    def settled(self, job: Job) -> Job:
        self.settled_ids.append(job.id)
        saved = self.unsaved == 0
        self.unsaved = max(0, self.unsaved - 1)
        latest = self.records.get((job.kind, job.id), job)
        if job.is_terminal:
            return latest.model_copy(update={"event_processing_finished": saved})
        return latest


def _no_hosts(job: Job) -> list[dict[str, Any]]:
    return []


def _specs(kind: str) -> AwxResourceSpec:
    return AwxResourceCatalog().get(kind)


def _no_nodes(job: Job) -> list[dict[str, Any]]:
    raise AssertionError("a job case reads no workflow nodes")


def _no_approvals(approval_id: int, *, approve: bool) -> None:
    raise AssertionError("a job case answers no approval")


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


def _exit_code(outcome: SuiteRunOutcome) -> int:
    """The exit code ``awx test run`` gives ``outcome``: the most severe counted failure."""
    with diagnostics_scope():
        for failure in outcome.counted():
            note_failure(failure)
        return failure_exit_code()


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
    job_reader: StubJobReader | None = None,
    log_reader: StubLogReader | None = None,
    event_reader: StubEventReader | None = None,
    preflight: LaunchCheck | None = None,
    host_reader: Any = _no_hosts,
    hosts: bool = False,
) -> RunTestSuite:
    resolver = ResolveCasePayload(
        fk, catalog=AwxResourceCatalog(), default_organization=default_org
    )
    jt_scope = {"organization": default_org} if default_org is not None else None
    log_reader = log_reader or StubLogReader([])
    return RunTestSuite(
        resolver=resolver,
        launcher=cast(Launcher, launcher),
        watcher=cast(Watcher, watcher),
        specs=_specs,
        node_reader=_no_nodes,
        approver=_no_approvals,
        fk_prefetcher=cast(FkPrefetcher, fk),
        jt_scope=jt_scope,
        canceller=canceller,
        job_reader=job_reader or StubJobReader(),
        log_reader=log_reader,
        event_reader=event_reader or StubEventReader([]),
        tail_reader=log_reader.tail,
        job_url=lambda job: f"https://aap.example.com/{job.kind}s/{job.id}",
        preflight=preflight,
        host_reader=host_reader,
        hosts=hosts,
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
        specs=_specs,
        node_reader=_no_nodes,
        approver=_no_approvals,
        fk_prefetcher=cast(FkPrefetcher, fk),
        log_reader=StubLogReader([]),
        event_reader=StubEventReader([]),
        tail_reader=StubLogReader([]).tail,
        job_reader=StubJobReader(),
        host_reader=_no_hosts,
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
    assert bool(outcome.counted()) == (result != "pass")
    if result == "error":
        assert row.job_id is None
        assert row.failure is not None
        assert "boom" in row.failure.message


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
    assert result.failure is not None
    assert result.failure.message == f"still running after 60s; {reason}"
    if canceller is not None:
        assert canceller.calls == [5]


def test_timeout_reports_a_job_that_ended_before_its_cancel() -> None:
    runner = _make_runner(
        fk=StubFk(),
        launcher=StubLauncher({"__default__": {"job": _job(id_=5, status="pending")}}),
        watcher=StubWatcher(default=_job(id_=5, status="running")),
        canceller=StubCanceller(fail_ids=frozenset({5})),
        job_reader=StubJobReader({("job", 5): _job(id_=5, status="successful")}),
    )
    [result] = runner([_suite("s", {"a": {}})], timeout=60).results
    assert result.result == "timeout"
    assert result.job_status == "successful"
    assert result.failure is not None
    assert result.failure.message == (
        "still running after 60s; it ended (successful) before the cancel"
    )


def test_polling_error_cancels_the_job() -> None:
    class FailingWatcher:
        def __call__(self, job: Job, *, timeout: float | None = None) -> Job:
            raise HttpTransportError("503 Service Unavailable", system="awx")

    canceller = StubCanceller()
    runner = _make_runner(
        fk=StubFk(),
        launcher=StubLauncher({"__default__": {"job": _job(id_=5, status="pending")}}),
        watcher=cast(Any, FailingWatcher()),
        canceller=canceller,
    )
    [result] = runner([_suite("s", {"a": {}})]).results
    assert result.failure is not None
    assert (result.result, result.failure.system, result.failure.message) == (
        "error",
        "awx.controller",
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
    assert row.failure is None
    # no log checks and nothing failed: nothing read
    assert (reader.calls, reader.tail_calls) == ([], [])
    assert row.job_url == "https://aap.example.com/jobs/5"


def test_failed_log_checks_are_the_expectation_with_a_tail_of_the_read_log() -> None:
    lines = [f"line {i}" for i in range(60)] + ["fatal: boom"]
    reader = StubLogReader(lines)
    runner, _ = _expect_runner("successful", reader)
    suite = _case_suite(
        {"expect": {"log": {"contains": ["PLAY RECAP"]}}},
        defaults={"expect": {"log": {"not_contains": ["fatal:"]}}},
    )
    [row] = runner([suite]).results
    assert row.result == "fail"
    assert row.failure is not None
    assert (row.failure.system, row.failure.category) == ("awx.expectation", "failed")
    assert row.failure.message == (
        "no log line contains 'PLAY RECAP'; log line contains 'fatal:': fatal: boom"
    )
    assert row.failure.evidence.log_tail == tuple(lines[-LOG_TAIL_LINES:])
    assert reader.tail_calls == []  # the downloaded log already has the tail


def test_a_failed_job_is_the_playbooks_with_a_tail_from_the_newest_events() -> None:
    reader = StubLogReader(["a", "b"])
    runner, _ = _expect_runner("failed", reader)
    [row] = runner([_case_suite({})]).results
    assert row.failure is not None
    assert (row.result, row.failure.system, row.failure.evidence.log_tail) == (
        "fail",
        "awx.playbook",
        ("a", "b"),
    )
    assert (reader.calls, reader.tail_calls) == ([], [("job", 5)])


def test_an_unreadable_log_errors_only_when_nothing_else_failed() -> None:
    reader = StubLogReader([], error=HttpTransportError("502 Bad Gateway", system="awx"))
    runner, _ = _expect_runner("failed", reader)
    [row] = runner([_case_suite({})]).results
    assert row.failure is not None
    assert (row.result, row.failure.evidence.log_tail) == ("fail", None)

    # The job failed: its attribution stands, the download is a note.
    runner, _ = _expect_runner("failed", reader)
    [row] = runner([_case_suite({"expect": {"log": {"contains": ["x"]}}})]).results
    assert row.failure is not None
    assert (row.result, row.failure.system, row.failure.evidence.note) == (
        "fail",
        "awx.playbook",
        "log fetch failed: 502 Bad Gateway",
    )

    # It succeeded: without the log the case cannot be checked.
    runner, _ = _expect_runner("successful", reader)
    [row] = runner([_case_suite({"expect": {"log": {"contains": ["x"]}}})]).results
    assert row.failure is not None
    assert (row.result, row.failure.system, row.failure.message) == (
        "error",
        "awx.controller",
        "log fetch failed: 502 Bad Gateway",
    )
    assert [check.check for check in row.expectations] == ["status"]


def test_a_log_fetch_failure_counts_toward_the_exit_code() -> None:
    reader = StubLogReader([], error=HttpTransportError("down", system="awx"))
    runner, _ = _expect_runner("successful", reader)
    assert _exit_code(runner([_case_suite({"expect": {"log": {"contains": ["x"]}}})])) == 5


def test_evidence_is_skipped_when_not_wanted_but_the_failure_is_attributed() -> None:
    reader = StubLogReader(["boom"])
    events = StubEventReader([_event("runner_on_unreachable", failed=True, host="db1", msg="x")])
    runner = RunTestSuite(
        resolver=ResolveCasePayload(StubFk(), catalog=AwxResourceCatalog()),
        launcher=cast(Launcher, StubLauncher({})),
        watcher=cast(Watcher, StubWatcher(default=_job(status="failed"))),
        specs=_specs,
        node_reader=_no_nodes,
        approver=_no_approvals,
        fk_prefetcher=cast(FkPrefetcher, StubFk()),
        log_reader=reader,
        event_reader=events,
        tail_reader=reader.tail,
        job_reader=StubJobReader(),
        host_reader=_no_hosts,
        job_url=lambda job: None,
        evidence=False,
    )
    [row] = runner([_case_suite({})]).results
    assert row.failure is not None
    assert (row.result, row.failure.system) == ("fail", "awx.hosts")
    assert row.failure.evidence == FailureEvidence()
    assert (reader.calls, reader.tail_calls, len(events.calls)) == ([], [], 1)


@pytest.mark.parametrize(
    ("status", "system"), [("running", "awx.playbook"), ("pending", "awx.controller")]
)
def test_a_timed_out_case_keeps_its_log_tail(status: str, system: str) -> None:
    runner, _ = _expect_runner(status, StubLogReader(["TASK [slow]"]))
    [row] = runner([_case_suite({})], timeout=5).results
    assert row.failure is not None
    assert (row.result, row.failure.system, row.failure.evidence.log_tail) == (
        "timeout",
        system,
        ("TASK [slow]",),
    )


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
    assert row.failure is not None
    evidence = row.failure.evidence
    assert evidence.failed_tasks is not None
    assert [(task.host, task.status, task.msg) for task in evidence.failed_tasks] == [
        ("web1", "failed", "boom"),
        ("db1", "unreachable", "ssh timeout"),
    ]
    assert evidence.unreachable_hosts == ("db1",)
    assert row.failure.message == "task 'Deploy' failed on web1: boom"
    # read once, for the attribution and the evidence
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
    assert (row.failure, events.calls) == (None, [])

    runner, _ = _expect_runner("failed", event_reader=StubEventReader([], error=RuntimeError()))
    [row] = runner([_case_suite({})]).results
    assert row.failure is not None
    # Unreadable events never blame the playbook.
    assert (row.result, row.failure.system, row.failure.evidence.failed_tasks) == (
        "fail",
        "awx.controller",
        None,
    )


@pytest.mark.parametrize(
    ("unsaved", "tasks", "system"),
    [(0, (), "awx.playbook"), (5, None, "awx.controller")],
)
def test_attribution_waits_for_the_events_and_never_trusts_an_unsaved_empty_list(
    unsaved: int, tasks: tuple[()] | None, system: str
) -> None:
    final = Job.model_validate({"id": 5, "kind": "job", "status": "failed"})
    reader = StubJobReader(unsaved=unsaved)
    runner = _make_runner(
        fk=StubFk(),
        launcher=StubLauncher({"__default__": {"job": _job(id_=5, status="pending")}}),
        watcher=StubWatcher(default=final),
        job_reader=reader,
    )
    [row] = runner([_case_suite({})]).results
    assert row.failure is not None
    assert (row.failure.system, row.failure.evidence.failed_tasks) == (system, tasks)
    assert reader.settled_ids == [5]
    if unsaved:
        assert "AWX has not processed its events yet" in row.failure.message


_PROJECT_FAILED = (
    'Previous Task Failed: {"job_type": "project_update", "job_name": "acme", "job_id": "812"}'
)


class EventsByExecution(StubEventReader):
    """Serves each execution kind its own events."""

    def __init__(self, by_kind: dict[str, list[JobEvent]]) -> None:
        super().__init__([])
        self.by_kind = by_kind

    def __call__(
        self, job: Job, *, params: dict[str, str] | None = None, follow: bool = True
    ) -> list[JobEvent]:
        super().__call__(job, params=params, follow=follow)
        return self.by_kind.get(job.kind, [])


def test_a_failed_project_update_is_the_scms_with_its_own_evidence() -> None:
    final = Job(id=5, kind="job", status="error", job_explanation=_PROJECT_FAILED)
    events = EventsByExecution(
        {
            "project_update": [
                _event("runner_on_failed", failed=True, host="localhost", msg="no ref feature/x")
            ]
        }
    )
    reader = StubLogReader(["fatal: couldn't find remote ref feature/x"])
    update = Job(id=812, kind="project_update", name="acme", status="failed")
    jobs = StubJobReader({("project_update", 812): update})
    runner = _make_runner(
        fk=StubFk(),
        launcher=StubLauncher({"__default__": {"job": _job(id_=5, status="pending")}}),
        watcher=StubWatcher(default=final),
        log_reader=reader,
        event_reader=events,
        job_reader=jobs,
    )
    outcome = runner([_case_suite({})])
    assert _exit_code(outcome) == 1
    [row] = outcome.results
    assert row.failure is not None
    assert (row.failure.system, row.failure.category, row.failure.message) == (
        "awx.scm",
        "failed",
        "project update 812 for 'acme' failed: no ref feature/x",
    )
    evidence = row.failure.evidence
    assert evidence.related is not None
    assert evidence.related.model_dump() == {
        "kind": "project_update",
        "id": 812,
        "name": "acme",
        "status": "failed",
        "url": "https://aap.example.com/project_updates/812",
    }
    assert evidence.job_explanation == _PROJECT_FAILED
    assert evidence.log_tail == ("fatal: couldn't find remote ref feature/x",)
    assert evidence.failed_tasks is not None
    assert [task.msg for task in evidence.failed_tasks] == ["no ref feature/x"]
    # the update's events and tail, never the job's own
    assert [call[0] for call in events.calls] == [812]
    assert reader.tail_calls == [("project_update", 812)]


_INVENTORY_FAILED = (
    'Previous Task Failed: {"job_type": "inventory_update", "job_name": "Cloud", "job_id": "9"}'
)


@pytest.mark.parametrize(
    ("final", "events", "exit_code"),
    [
        (Job(id=5, kind="job", status="failed", job_explanation=_INVENTORY_FAILED), [], 4),
        (
            Job(id=5, kind="job", status="failed"),
            [_event("runner_on_unreachable", failed=True, host="db1", msg="ssh timeout")],
            5,
        ),
        (Job(id=5, kind="job", status="error", job_explanation="pod failed"), [], 5),
        (Job(id=5, kind="job", status="failed"), [], 1),
    ],
)
def test_each_case_failure_counts_toward_the_exit_code_by_its_category(
    final: Job, events: list[JobEvent], exit_code: int
) -> None:
    runner = _make_runner(
        fk=StubFk(),
        launcher=StubLauncher({"__default__": {"job": _job(id_=5, status="pending")}}),
        watcher=StubWatcher(default=final),
        event_reader=StubEventReader(events),
    )
    assert _exit_code(runner([_case_suite({})])) == exit_code


def test_the_most_severe_case_failure_wins() -> None:
    finals = {
        1: Job(id=1, kind="job", status="failed"),
        2: Job(id=2, kind="job", status="error"),
    }
    launcher = StubLauncher(
        {
            "a": {"job": _job(id_=1, status="pending")},
            "b": {"job": _job(id_=2, status="pending")},
        }
    )
    runner = _make_runner(fk=StubFk(), launcher=launcher, watcher=StubWatcher(by_id=finals))
    suite = _suite("s", {name: {"extra_vars": {"case_name": name}} for name in ("a", "b")})
    outcome = runner([suite])
    assert _exit_code(outcome) == 5
    assert [row.failure.system for row in outcome.results if row.failure] == [
        "awx.playbook",
        "awx.controller",
    ]


def test_every_case_with_a_job_carries_its_host_summaries() -> None:
    read: list[int] = []

    def hosts(job: Job) -> list[dict[str, Any]]:
        read.append(job.id)
        return [{"host_name": "web1", "ok": 3, "changed": 1, "dark": 0}]

    runner = _make_runner(
        fk=StubFk(),
        launcher=StubLauncher({"__default__": {"job": _job(id_=5, status="pending")}}),
        watcher=StubWatcher(default=_job(id_=5, status="successful")),
        host_reader=hosts,
        hosts=True,
    )
    [row] = runner([_case_suite({})]).results
    assert row.hosts is not None
    assert row.hosts["web1"].model_dump() == {
        "ok": 3,
        "changed": 1,
        "failed": 0,
        "unreachable": 0,
        "skipped": 0,
        "rescued": 0,
        "ignored": 0,
    }
    assert (row.hosts_truncated, read) == (False, [5])


def test_unreadable_or_unwanted_host_summaries_are_null() -> None:
    def broken(job: Job) -> list[dict[str, Any]]:
        raise RuntimeError("503")

    for reader, wanted in ((broken, True), (_no_hosts, False)):
        runner = _make_runner(
            fk=StubFk(),
            launcher=StubLauncher({"__default__": {"job": _job(id_=5, status="pending")}}),
            watcher=StubWatcher(default=_job(id_=5, status="successful")),
            host_reader=reader,
            hosts=wanted,
        )
        [row] = runner([_case_suite({})]).results
        assert (row.result, row.hosts, row.hosts_truncated) == ("pass", None, False)


def test_a_negative_case_whose_update_failed_does_not_pass() -> None:
    """``expect: status: failed`` must not pass when the playbook never ran."""
    final = Job(id=5, kind="job", status="failed", job_explanation=_PROJECT_FAILED)
    update = Job(id=812, kind="project_update", name="acme", status="failed")
    runner = _make_runner(
        fk=StubFk(),
        launcher=StubLauncher({"__default__": {"job": _job(id_=5, status="pending")}}),
        watcher=StubWatcher(default=final),
        job_reader=StubJobReader({("project_update", 812): update}),
    )
    [row] = runner([_case_suite({"expect": {"status": "failed"}})]).results
    assert row.failure is not None
    assert (row.result, row.failure.system) == ("fail", "awx.scm")
    assert [check.passed for check in row.expectations] == [True]


def test_rescued_failures_do_not_blame_the_playbook() -> None:
    """A table run still reads a failed job's host summaries to drop rescued failures."""
    events = StubEventReader(
        [
            _event("runner_on_failed", failed=True, host="web1", msg="rescued"),
            _event("runner_on_unreachable", failed=True, host="db1", msg="ssh timeout"),
        ]
    )
    read: list[int] = []

    def hosts(job: Job) -> list[dict[str, Any]]:
        read.append(job.id)
        return [{"host_name": "web1", "failures": 0, "rescued": 1}, {"host_name": "db1", "dark": 1}]

    runner = _make_runner(
        fk=StubFk(),
        launcher=StubLauncher({"__default__": {"job": _job(id_=5, status="pending")}}),
        watcher=StubWatcher(default=_job(id_=5, status="failed")),
        event_reader=events,
        host_reader=hosts,
    )
    [row] = runner([_case_suite({})]).results
    assert row.failure is not None
    assert (row.failure.system, row.failure.message) == (
        "awx.hosts",
        "unreachable: db1: ssh timeout",
    )
    assert read == [5]


def test_a_passing_case_reads_no_events_nor_hosts_unless_asked() -> None:
    events = StubEventReader([])
    read: list[int] = []

    def hosts(job: Job) -> list[dict[str, Any]]:
        read.append(job.id)
        return []

    runner = _make_runner(
        fk=StubFk(),
        launcher=StubLauncher({"__default__": {"job": _job(id_=5, status="pending")}}),
        watcher=StubWatcher(default=_job(id_=5, status="failed")),
        event_reader=events,
        host_reader=hosts,
    )
    [row] = runner([_case_suite({"expect": {"status": "failed"}})]).results
    assert (row.result, events.calls, read) == ("pass", [], [])


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
        spec: object,
        *,
        name: str,
        scope: dict[str, str] | None,
        payload: dict[str, Any],
        nodes: Any = (),
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
    assert (info.value.category, info.value.system) == ("invalid", "awx.suite")


def test_a_preflight_failure_carries_its_most_severe_category() -> None:
    def preflight(
        spec: object,
        *,
        name: str,
        scope: dict[str, str] | None,
        payload: dict[str, Any],
        nodes: Any = (),
    ) -> None:
        if "limit" in payload:
            raise LaunchPromptError("does not prompt for limit")
        raise ConfigError(
            "AWX rejected the token (HTTP 401)",
            category="auth",
            system="awx",
            hint="run `untaped config set awx.token --prompt`",
        )

    runner = _make_runner(
        fk=StubFk(), launcher=StubLauncher({}), watcher=StubWatcher(), preflight=preflight
    )
    suite = _suite("s", {"bad": {"limit": "web"}, "denied": {}})
    with pytest.raises(ConfigError) as info:
        runner([suite])
    assert (info.value.category, info.value.system) == ("auth", "awx.credentials")
    assert info.value.exit_code == 4
    assert info.value.hint == "run `untaped config set awx.token --prompt`"


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
        spec: object,
        *,
        name: str,
        scope: dict[str, str] | None,
        payload: dict[str, Any],
        nodes: Any = (),
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


# ---- regression expectations -------------------------------------------


class SequenceLauncher(StubLauncher):
    """Launches a new job per call: ids 5, 6, 7, … in order."""

    def __init__(self) -> None:
        super().__init__({})
        self.next_id = 5

    def __call__(self, spec: AwxResourceSpec, **kwargs: Any) -> Job:
        super().__call__(spec, **kwargs)
        job = _job(id_=self.next_id, status="pending")
        self.next_id += 1
        return job


class HostsById:
    """Serves each job its own host summary records, filtered as AWX filters them.

    Records which jobs were read (``read``) and each filtered read (``filtered``).
    """

    def __init__(self, by_id: dict[int, list[dict[str, Any]]]) -> None:
        self.by_id = by_id
        self.read: list[int] = []
        self.filtered: list[tuple[int, dict[str, str]]] = []

    def __call__(self, job: Job, params: dict[str, str] | None = None) -> list[dict[str, Any]]:
        records = self.by_id.get(job.id, [])
        if params is None:
            self.read.append(job.id)
            return records
        self.filtered.append((job.id, params))
        [(key, value)] = params.items()
        if key == "host_name__in":
            return [r for r in records if r["host_name"] in value.split(",")]
        field = key.removesuffix("__gt")
        return [r for r in records if (r.get(field) or 0) > int(value)]


class EventsById(StubEventReader):
    """Serves each job its own events."""

    def __init__(self, by_id: dict[int, list[JobEvent]]) -> None:
        super().__init__([])
        self.by_id = by_id

    def __call__(
        self, job: Job, *, params: dict[str, str] | None = None, follow: bool = True
    ) -> list[JobEvent]:
        super().__call__(job, params=params, follow=follow)
        return self.by_id.get(job.id, [])


def _regression_runner(
    finals: dict[int, str | Job],
    *,
    hosts: dict[int, list[dict[str, Any]]] | None = None,
    events: dict[int, list[JobEvent]] | None = None,
    canceller: StubCanceller | None = None,
    evidence: bool = True,
    watcher: Any = None,
    job_reader: Any = None,
    job_url: Any = None,
    stop: threading.Event | None = None,
    fk: StubFk | None = None,
    host_reader: HostsById | None = None,
) -> tuple[RunTestSuite, SequenceLauncher, HostsById, EventsById]:
    launcher = SequenceLauncher()
    host_reader = host_reader or HostsById(hosts or {})
    event_reader = EventsById(events or {})
    by_id = {
        id_: final if isinstance(final, Job) else _job(id_=id_, status=final)
        for id_, final in finals.items()
    }
    runner = RunTestSuite(
        resolver=ResolveCasePayload(fk or StubFk(), catalog=AwxResourceCatalog()),
        launcher=cast(Launcher, launcher),
        watcher=cast(Watcher, watcher or StubWatcher(by_id=by_id)),
        specs=_specs,
        node_reader=_no_nodes,
        approver=_no_approvals,
        fk_prefetcher=cast(FkPrefetcher, fk or StubFk()),
        log_reader=StubLogReader(["ok"]),
        event_reader=event_reader,
        tail_reader=StubLogReader(["tail"]).tail,
        job_reader=job_reader or StubJobReader(),
        host_reader=host_reader,
        job_url=job_url or (lambda job: None),
        canceller=canceller,
        evidence=evidence,
        stop=stop,
    )
    return runner, launcher, host_reader, event_reader


def test_host_expectations_read_the_summaries_even_for_a_table() -> None:
    runner, _, hosts, _ = _regression_runner(
        {5: "successful"},
        hosts={5: [{"host_name": "web1", "changed": 2}, {"host_name": "web2", "failures": 1}]},
    )
    suite = _case_suite({"expect": {"changed": 0, "hosts": {"*": {"failed": 0}}}})

    [row] = runner([suite]).results

    assert hosts.read == [5]
    assert row.hosts is not None and set(row.hosts) == {"web1", "web2"}
    assert row.failure is not None
    assert (row.result, row.failure.system, row.failure.category) == (
        "fail",
        "awx.expectation",
        "failed",
    )
    assert row.failure.message == (
        "expected <= 0 changed tasks, got 2; expected *: failed <= 0, got web2=1"
    )
    assert [check.check for check in row.expectations] == ["status", "changed", "hosts"]


def test_host_bounds_that_hold_pass() -> None:
    runner, _, _, _ = _regression_runner(
        {5: "successful"}, hosts={5: [{"host_name": "web1", "ok": 3}]}
    )
    suite = _case_suite({"expect": {"changed": 0, "hosts": {"web1": {"failed": 0}}}})
    [row] = runner([suite]).results
    assert (row.result, row.failure) == ("pass", None)


def test_unreadable_summaries_a_check_needs_are_the_cases_error() -> None:
    def broken(job: Job) -> list[dict[str, Any]]:
        raise HttpTransportError("503 Service Unavailable", system="awx")

    runner = _make_runner(
        fk=StubFk(),
        launcher=StubLauncher({"__default__": {"job": _job(id_=5, status="pending")}}),
        watcher=StubWatcher(default=_job(id_=5, status="successful")),
        host_reader=broken,
    )
    outcome = runner([_case_suite({"expect": {"changed": 0}})])
    [row] = outcome.results
    assert row.failure is not None
    assert (row.result, row.failure.system, row.failure.message) == (
        "error",
        "awx.controller",
        "host summaries unreadable: 503 Service Unavailable",
    )
    assert _exit_code(outcome) == 5


_VALIDATE_FAILED = _event("runner_on_failed", failed=True, host="web1", msg="env must be one of")


def test_a_negative_case_passes_only_when_its_failed_tasks_match() -> None:
    body = {"expect": {"status": "failed", "failed_tasks": [{"task": "Dep", "msg": "one of"}]}}
    runner, _, hosts, events = _regression_runner(
        {5: "failed"},
        hosts={5: [{"host_name": "web1", "failures": 1}]},
        events={5: [_VALIDATE_FAILED]},
    )
    [row] = runner([_case_suite(body)]).results
    assert (row.result, row.failure) == ("pass", None)
    assert row.expectations[1].model_dump() == {
        "check": "failed_tasks",
        "expected": "task 'Dep', msg 'one of'",
        "actual": "[web1] Deploy: env must be one of",
        "passed": True,
    }
    assert (hosts.read, len(events.calls)) == ([5], 1)

    body = {"expect": {"status": "failed", "failed_tasks": [{"matches": "^version"}]}}
    runner, _, _, _ = _regression_runner(
        {5: "failed"},
        hosts={5: [{"host_name": "web1", "failures": 1}]},
        events={5: [_VALIDATE_FAILED]},
    )
    [row] = runner([_case_suite(body)]).results
    assert row.failure is not None
    assert (row.result, row.failure.system, row.failure.message) == (
        "fail",
        "awx.expectation",
        "no failed task matches msg matches '^version'",
    )
    assert row.failure.evidence.failed_tasks is not None
    assert [task.msg for task in row.failure.evidence.failed_tasks] == ["env must be one of"]


def test_a_rescued_failure_does_not_match_a_failed_task() -> None:
    body = {"expect": {"status": "failed", "failed_tasks": [{"msg": "one of"}]}}
    runner, _, _, _ = _regression_runner(
        {5: "failed"},
        hosts={5: [{"host_name": "web1", "failures": 0, "rescued": 1}]},
        events={5: [_VALIDATE_FAILED]},
    )
    [row] = runner([_case_suite(body)]).results
    assert row.failure is not None
    assert (row.result, row.failure.message) == ("fail", "no failed task matches msg 'one of'")


def test_unreadable_events_leave_failed_tasks_unchecked() -> None:
    runner = _make_runner(
        fk=StubFk(),
        launcher=StubLauncher({"__default__": {"job": _job(id_=5, status="pending")}}),
        watcher=StubWatcher(default=_job(id_=5, status="failed")),
        event_reader=StubEventReader([], error=RuntimeError("down")),
    )
    body = {"expect": {"status": "failed", "failed_tasks": [{"msg": "x"}]}}
    [row] = runner([_case_suite(body)]).results
    assert row.failure is not None
    assert (row.result, row.failure.system, row.failure.message) == (
        "error",
        "awx.controller",
        "failed_tasks not checked: its events could not be read",
    )


def test_an_idempotent_case_relaunches_the_same_payload_and_passes_unchanged() -> None:
    runner, launcher, hosts, _ = _regression_runner(
        {5: "successful", 6: "successful"},
        hosts={5: [{"host_name": "web1", "changed": 3}], 6: [{"host_name": "web1", "ok": 3}]},
    )
    body = {"launch": {"limit": "web1"}, "expect": {"idempotent": True}}

    [row] = runner([_case_suite(body)]).results

    assert [call["payload"] for call in launcher.calls] == [{"limit": "web1"}] * 2
    assert (row.result, row.job_id, row.rerun_job_id, row.failure) == ("pass", 5, 6, None)
    assert row.expectations[-1].model_dump() == {
        "check": "idempotent",
        "expected": "successful, 0 changed",
        "actual": "successful, 0 changed",
        "passed": True,
    }
    assert hosts.read == [6]  # only the rerun's changes matter


def _changed(host: str, task: str) -> JobEvent:
    return JobEvent.model_validate(
        {"counter": 1, "event": "runner_on_ok", "changed": True, "host_name": host, "task": task}
    )


def test_a_rerun_that_changes_something_lists_what_changed() -> None:
    runner, _, _, events = _regression_runner(
        {5: "successful", 6: "successful"},
        hosts={6: [{"host_name": "web1", "changed": 1}, {"host_name": "web2", "changed": 1}]},
        events={6: [_changed("web1", "Template config"), _changed("web2", "Restart")]},
    )

    outcome = runner([_case_suite({"expect": {"idempotent": True}})])

    [row] = outcome.results
    assert row.failure is not None
    assert (row.result, row.rerun_job_id, row.failure.system, row.failure.message) == (
        "fail",
        6,
        "awx.expectation",
        "not idempotent: the rerun ended successful, 2 changed",
    )
    assert row.failure.evidence.changed_tasks is not None
    assert [task.model_dump() for task in row.failure.evidence.changed_tasks] == [
        {"host": "web1", "task": "Template config"},
        {"host": "web2", "task": "Restart"},
    ]
    assert (6, {"event": "runner_on_ok", "changed": "true"}, False) in events.calls
    assert row.expectations[-1].actual == "successful, 2 changed"
    assert _exit_code(outcome) == 1


def test_without_evidence_a_rerun_reads_no_changed_tasks() -> None:
    runner, _, _, events = _regression_runner(
        {5: "successful", 6: "successful"},
        hosts={6: [{"host_name": "web1", "changed": 1}]},
        evidence=False,
    )
    [row] = runner([_case_suite({"expect": {"idempotent": True}})]).results
    assert row.failure is not None
    assert (row.failure.message, row.failure.evidence.changed_tasks) == (
        "not idempotent: the rerun ended successful, 1 changed",
        None,
    )
    assert events.calls == []


def test_a_rerun_that_fails_is_attributed_like_any_job() -> None:
    runner, _, _, _ = _regression_runner(
        {5: "successful", 6: "failed"},
        events={6: [_event("runner_on_failed", failed=True, host="web1", msg="already exists")]},
    )
    [row] = runner([_case_suite({"expect": {"idempotent": True}})]).results
    assert row.failure is not None
    assert (row.result, row.job_status, row.failure.system, row.failure.message) == (
        "fail",
        "successful",
        "awx.playbook",
        "rerun job 6: task 'Deploy' failed on web1: already exists",
    )
    assert row.expectations[-1].actual == "failed, 0 changed"


def test_a_rerun_that_times_out_is_cancelled() -> None:
    canceller = StubCanceller()
    runner, _, _, _ = _regression_runner({5: "successful", 6: "running"}, canceller=canceller)
    [row] = runner([_case_suite({"expect": {"idempotent": True}})], timeout=30).results
    assert row.failure is not None
    assert (row.result, row.rerun_job_id, row.failure.message) == (
        "timeout",
        6,
        "rerun job 6: still running after 30s; cancel requested",
    )
    assert canceller.calls == [6]


def test_a_case_that_failed_is_not_rerun() -> None:
    runner, launcher, _, _ = _regression_runner({5: "failed"})
    [row] = runner([_case_suite({"expect": {"idempotent": True}})]).results
    assert (row.result, row.rerun_job_id, len(launcher.calls)) == ("fail", None, 1)


def test_a_rerun_the_controller_refuses_keeps_its_error() -> None:
    launcher = SequenceLauncher()

    def launch(spec: AwxResourceSpec, **kwargs: Any) -> Job:
        if launcher.next_id == 6:
            raise HttpTransportError("503 Service Unavailable", system="awx")
        return launcher(spec, **kwargs)

    runner = _make_runner(
        fk=StubFk(),
        launcher=cast(Any, launch),
        watcher=StubWatcher(default=_job(id_=5, status="successful")),
    )
    [row] = runner([_case_suite({"expect": {"idempotent": True}})]).results
    assert row.failure is not None
    assert (row.result, row.rerun_job_id, row.failure.system, row.failure.message) == (
        "error",
        None,
        "awx.controller",
        "the rerun: 503 Service Unavailable",
    )
    assert row.expectations[-1].actual == "not launched"


class WatchFails(StubWatcher):
    """Raises ``error`` while watching job ``id_``; every other job ends ``successful``."""

    def __init__(self, id_: int, error: BaseException) -> None:
        super().__init__()
        self.id_, self.error = id_, error

    def __call__(self, job: Job, *, timeout: float | None = None) -> Job:
        if job.id == self.id_:
            raise self.error
        return _job(id_=job.id, status="successful")


def test_a_rerun_whose_polling_failed_reports_an_unknown_status() -> None:
    canceller = StubCanceller()
    error = HttpTransportError("503 Service Unavailable", system="awx")
    runner, _, _, _ = _regression_runner({}, watcher=WatchFails(6, error), canceller=canceller)
    [row] = runner([_case_suite({"expect": {"idempotent": True}})]).results
    assert row.failure is not None
    assert (row.result, row.rerun_job_id, row.failure.message) == (
        "error",
        6,
        "rerun job 6: 503 Service Unavailable; cancel requested",
    )
    assert row.expectations[-1].actual == "unknown"
    assert canceller.calls == [6]


def test_ctrl_c_during_a_rerun_cancels_it() -> None:
    canceller = StubCanceller()
    runner, _, _, _ = _regression_runner(
        {}, watcher=WatchFails(6, KeyboardInterrupt()), canceller=canceller
    )
    with pytest.raises(KeyboardInterrupt):
        runner([_case_suite({"expect": {"idempotent": True}})])
    assert canceller.calls == [6]


def test_a_stopping_run_launches_no_rerun() -> None:
    stop = threading.Event()

    class StopsWhileWatching(StubWatcher):
        def __call__(self, job: Job, *, timeout: float | None = None) -> Job:
            stop.set()
            return _job(id_=job.id, status="successful")

    runner, launcher, _, _ = _regression_runner({}, watcher=StopsWhileWatching(), stop=stop)
    [row] = runner([_case_suite({"expect": {"idempotent": True}})]).results
    assert (row.rerun_job_id, len(launcher.calls)) == (None, 1)


def test_a_rerun_runs_the_commit_the_first_job_ran() -> None:
    first = Job(
        id=5, kind="job", status="successful", scm_branch="feature/x", scm_revision="c0ffee"
    )
    runner, launcher, _, _ = _regression_runner({5: first, 6: "successful"})
    body = {"launch": {"scm_branch": "feature/x"}, "expect": {"idempotent": True}}

    [row] = runner([_case_suite(body)]).results

    assert row.result == "pass"
    assert [call["payload"]["scm_branch"] for call in launcher.calls] == ["feature/x", "c0ffee"]


class SettleFails(StubJobReader):
    def settled(self, job: Job) -> Job:
        if job.id == 5:
            raise HttpTransportError("502 Bad Gateway", system="awx")
        return super().settled(job)


def test_a_job_that_cannot_be_settled_is_the_cases_error() -> None:
    runner, _, _, _ = _regression_runner({5: "failed", 6: "failed"}, job_reader=SettleFails())
    suite = _suite("s", {"a": {}, "b": {}})

    outcome = runner([suite])

    first, second = outcome.results
    assert first.failure is not None
    assert (first.result, first.job_id, first.failure.system, first.failure.message) == (
        "error",
        5,
        "awx.controller",
        "502 Bad Gateway",
    )
    assert second.result == "fail"
    assert _exit_code(outcome) == 5


def test_an_aborted_run_still_counts_the_cases_that_finished() -> None:
    def job_url(job: Job) -> str:
        if job.id == 6:
            raise RuntimeError("bug")
        return "url"

    runner, _, _, _ = _regression_runner(
        {5: Job(id=5, kind="job", status="error", job_explanation="pod lost"), 6: "successful"},
        job_url=job_url,
    )
    with diagnostics_scope():
        with pytest.raises(RuntimeError, match="bug"):
            runner([_suite("s", {"a": {}, "b": {}})])
        assert failure_exit_code() == 5


def test_an_aborted_run_still_counts_its_baseline_runs_failures() -> None:
    credential = "Credential lookup failed: vault denied"
    finals: dict[int, str | Job] = {
        5: Job(id=5, kind="job", status="error", job_explanation=credential),
        6: "successful",
    }

    def job_url(job: Job) -> str:
        if job.id == 6:
            raise RuntimeError("bug")
        return "url"

    runner, _, _, _ = _regression_runner(finals, job_url=job_url)
    with diagnostics_scope():
        with pytest.raises(RuntimeError, match="bug"):
            runner([_case_suite({})], baseline="main")
        assert failure_exit_code() == 4


def test_baseline_runs_every_case_at_the_ref_first_then_compares() -> None:
    fk = StubFk()
    runner, launcher, _, _ = _regression_runner({5: "failed", 6: "successful"}, fk=fk)

    outcome = runner([_case_suite({})], baseline="main", scm_branch="fix")

    assert [call["payload"].get("scm_branch") for call in launcher.calls] == ["main", "fix"]
    assert [call[0] for call in fk.calls].count("prefetch") == 1
    [row] = outcome.results
    assert (row.job_id, row.change, row.baseline) == (
        6,
        "fixed",
        Baseline(result="fail", job_id=5, system="awx.playbook", category="failed"),
    )
    assert outcome.counted() == []


def test_the_baseline_runs_environment_failures_count() -> None:
    finals: dict[int, str | Job] = {
        5: Job(id=5, kind="job", status="error", job_explanation="pod lost"),
        6: "successful",
    }
    runner, _, _, _ = _regression_runner(finals)
    outcome = runner([_case_suite({})], baseline="main")
    [row] = outcome.results
    assert (row.change, [f.system for f in outcome.counted()]) == ("fixed", ["awx.controller"])


def test_a_saved_baseline_is_compared_with() -> None:
    runner, _, _, _ = _regression_runner({5: "failed"})
    saved = {("s", "c"): Baseline(result="pass", job_id=1)}
    [row] = runner([_case_suite({})], compare=saved).results
    assert (row.change, row.baseline) == ("regression", saved[("s", "c")])


def test_failed_tasks_on_a_successful_job_fail_without_reading_events() -> None:
    runner, _, hosts, events = _regression_runner({5: "successful"})
    body = {"expect": {"failed_tasks": [{"msg": "x"}], "log": {"contains": ["absent"]}}}

    [row] = runner([_case_suite(body)]).results

    assert row.failure is not None
    assert row.failure.message == "no log line contains 'absent'; no failed task matches msg 'x'"
    assert row.failure.evidence.failed_tasks == ()
    assert (events.calls, hosts.read) == ([], [])


def _many_hosts(**last: int) -> list[dict[str, Any]]:
    """501 hosts: the first 500 changed nothing, the last (cut from the list) ``last``."""
    hosts: list[dict[str, Any]] = [{"host_name": f"h{index:03}", "ok": 1} for index in range(500)]
    return [*hosts, {"host_name": "h500", **last}]


def test_a_cut_host_list_never_hides_a_changed_host() -> None:
    runner, _, hosts, _ = _regression_runner({5: "successful"}, hosts={5: _many_hosts(changed=3)})

    [row] = runner([_case_suite({"expect": {"changed": 0, "hosts": {"*": {"failed": 0}}}})]).results

    assert row.hosts is not None and (len(row.hosts), row.hosts_truncated) == (500, True)
    assert row.failure is not None
    assert row.failure.message == "expected <= 0 changed tasks, got 3"
    assert hosts.filtered == [(5, {"changed__gt": "0"}), (5, {"failures__gt": "0"})]


def test_a_cut_host_list_never_hides_a_change_on_the_rerun() -> None:
    runner, _, _, _ = _regression_runner(
        {5: "successful", 6: "successful"}, hosts={6: _many_hosts(changed=1)}, evidence=False
    )
    [row] = runner([_case_suite({"expect": {"idempotent": True}})]).results
    assert row.failure is not None
    assert row.failure.message == "not idempotent: the rerun ended successful, 1 changed"


def test_a_cut_host_list_that_cannot_be_completed_is_the_cases_error() -> None:
    class Broken(HostsById):
        def __call__(self, job: Job, params: dict[str, str] | None = None) -> list[dict[str, Any]]:
            if params is not None:
                raise HttpTransportError("503 Service Unavailable", system="awx")
            return super().__call__(job)

    runner, _, _, _ = _regression_runner({5: "successful"}, host_reader=Broken({5: _many_hosts()}))
    [row] = runner([_case_suite({"expect": {"changed": 0}})]).results
    assert row.failure is not None
    assert (row.result, row.failure.message) == (
        "error",
        "host summaries unreadable: 503 Service Unavailable",
    )
