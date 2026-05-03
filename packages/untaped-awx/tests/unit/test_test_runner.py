"""RunTestSuite: sequential + parallel runs, classification, ordering."""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest
from untaped_awx.application.test.resolver import ResolveCasePayload
from untaped_awx.application.test.runner import RunTestSuite
from untaped_awx.domain import Job
from untaped_awx.domain.test_suite import Case, TestSuite
from untaped_awx.infrastructure.spec import AwxResourceSpec
from untaped_awx.infrastructure.specs import JOB_TEMPLATE_SPEC


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


def _suite(name: str, cases: dict[str, dict[str, Any]]) -> TestSuite:
    return TestSuite(
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
) -> RunTestSuite:
    resolver = ResolveCasePayload(fk, default_organization=default_org)
    jt_scope = {"organization": default_org} if default_org is not None else None
    return RunTestSuite(
        resolver=resolver,
        launcher=launcher,  # type: ignore[arg-type]
        watcher=watcher,  # type: ignore[arg-type]
        spec=JOB_TEMPLATE_SPEC,
        fk_prefetcher=fk,  # type: ignore[arg-type]
        jt_scope=jt_scope,
    )


# ---- sequential runner --------------------------------------------------


def test_sequential_all_pass() -> None:
    fk = StubFk()
    launcher = StubLauncher({"__default__": {"job": _job(id_=42, status="successful")}})
    watcher = StubWatcher(default=_job(id_=42, status="successful"))
    runner = _make_runner(fk=fk, launcher=launcher, watcher=watcher)
    suite = _suite(
        "s", {"a": {"extra_vars": {"case_name": "a"}}, "b": {"extra_vars": {"case_name": "b"}}}
    )

    outcome = runner([suite])

    assert [r.case for r in outcome.results] == ["a", "b"]
    assert all(r.result == "pass" for r in outcome.results)
    assert outcome.exit_code() == 0


def test_failed_status_classified_as_fail() -> None:
    fk = StubFk()
    launcher = StubLauncher({"a": {"job": _job(id_=1, status="pending")}})
    watcher = StubWatcher(by_id={1: _job(id_=1, status="failed")})
    runner = _make_runner(fk=fk, launcher=launcher, watcher=watcher)
    suite = _suite("s", {"a": {"extra_vars": {"case_name": "a"}}})

    outcome = runner([suite])

    assert outcome.results[0].result == "fail"
    assert outcome.results[0].job_status == "failed"
    assert outcome.exit_code() == 1


def test_launcher_exception_classified_as_error() -> None:
    fk = StubFk()
    launcher = StubLauncher({"a": {"raises": RuntimeError("boom")}})
    watcher = StubWatcher()
    runner = _make_runner(fk=fk, launcher=launcher, watcher=watcher)
    suite = _suite("s", {"a": {"extra_vars": {"case_name": "a"}}})

    outcome = runner([suite])

    assert outcome.results[0].result == "error"
    assert outcome.results[0].job_id is None
    assert "boom" in (outcome.results[0].failure_reason or "")
    assert outcome.exit_code() == 1


def test_non_terminal_watch_classified_as_timeout() -> None:
    fk = StubFk()
    launcher = StubLauncher({"a": {"job": _job(id_=2, status="pending")}})
    watcher = StubWatcher(by_id={2: _job(id_=2, status="running")})  # never terminal
    runner = _make_runner(fk=fk, launcher=launcher, watcher=watcher)
    suite = _suite("s", {"a": {"extra_vars": {"case_name": "a"}}})

    outcome = runner([suite], timeout=1.0)

    assert outcome.results[0].result == "timeout"
    assert outcome.results[0].job_status == "running"


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


def test_case_filter_runs_only_selected() -> None:
    fk = StubFk()
    launcher = StubLauncher({})
    watcher = StubWatcher()
    runner = _make_runner(fk=fk, launcher=launcher, watcher=watcher)
    suite = _suite("s", {"keep": {"extra_vars": {"case_name": "keep"}}, "skip": {}})

    outcome = runner([suite], case_filter={"keep"})

    assert [r.case for r in outcome.results] == ["keep"]
    assert len(launcher.calls) == 1


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
    runner = _make_runner(fk=fk, launcher=launcher, watcher=watcher)  # type: ignore[arg-type]
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
