# tests/workspace/unit/test_run_use_case.py
"""RunInRepos scheduling, environment, failure rules — with a fake runner."""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from untaped.capabilities.workspace.application.run import RunInRepos, RunTarget
from untaped.capabilities.workspace.domain import CommandResult, RepoSpec


class FakeRunner:
    def __init__(self, codes: Mapping[str, int | None]) -> None:
        self.codes = codes
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.lock = threading.Lock()
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True

    def run(
        self, argv: Sequence[str], *, cwd: Path, env: Mapping[str, str], timeout: float
    ) -> CommandResult:
        with self.lock:
            self.calls.append((cwd.name, dict(env)))
        code = self.codes.get(cwd.name, 0)
        return CommandResult(
            returncode=code,
            stdout=f"out {cwd.name}",
            stderr="",
            duration_s=0.01,
            timed_out=code is None,
        )


def _targets(tmp_path: Path, *names: str, read_only: tuple[str, ...] = ()) -> list[RunTarget]:
    out = []
    for name in names:
        path = tmp_path / name
        path.mkdir(exist_ok=True)
        spec = RepoSpec(
            url=f"u/{name}",
            name=f"acme/{name}",
            dir=name,
            branch=None if name in read_only else "J-1",
            base="main",
        )
        out.append(RunTarget(workspace="J-1", spec=spec, path=path))
    return out


def test_every_repo_runs_with_context_env(tmp_path: Path) -> None:
    runner = FakeRunner({})
    rows = RunInRepos(runner, parallel=2, timeout=5, fail_fast=False)(
        _targets(tmp_path, "api", "web"), ["true"]
    )
    assert [r.action for r in rows] == ["ran", "ran"]
    env = dict(runner.calls)["api"]
    assert {
        k: env[k]
        for k in (
            "UNTAPED_WORKSPACE",
            "UNTAPED_REPO",
            "UNTAPED_BRANCH",
            "UNTAPED_BASE",
            "UNTAPED_READ_ONLY",
        )
    } == {
        "UNTAPED_WORKSPACE": "J-1",
        "UNTAPED_REPO": "acme/api",
        "UNTAPED_BRANCH": "J-1",
        "UNTAPED_BASE": "main",
        "UNTAPED_READ_ONLY": "0",
    }


def test_read_only_env(tmp_path: Path) -> None:
    runner = FakeRunner({})
    RunInRepos(runner, parallel=1, timeout=5, fail_fast=False)(
        _targets(tmp_path, "docs", read_only=("docs",)), ["true"]
    )
    env = dict(runner.calls)["docs"]
    assert (env["UNTAPED_BRANCH"], env["UNTAPED_READ_ONLY"]) == ("", "1")


def test_failures_do_not_stop_the_others(tmp_path: Path) -> None:
    rows = RunInRepos(FakeRunner({"api": 2}), parallel=1, timeout=5, fail_fast=False)(
        _targets(tmp_path, "api", "web"), ["x"]
    )
    assert [(r.action, r.returncode) for r in rows] == [("failed", 2), ("ran", 0)]
    assert rows[0].error is not None and rows[0].error.category == "failed"


def test_fail_fast_skips_unstarted_repos(tmp_path: Path) -> None:
    rows = RunInRepos(FakeRunner({"a": 1}), parallel=1, timeout=5, fail_fast=True)(
        _targets(tmp_path, "a", "b", "c"), ["x"]
    )
    assert [r.action for r in rows] == ["failed", "skipped", "skipped"]


def test_timeout_is_a_failure(tmp_path: Path) -> None:
    [row] = RunInRepos(FakeRunner({"api": None}), parallel=1, timeout=5, fail_fast=False)(
        _targets(tmp_path, "api"), ["x"]
    )
    assert (row.action, row.returncode, row.detail) == ("failed", None, "timed out after 5s")


class CancelledRunner(FakeRunner):
    def run(
        self, argv: Sequence[str], *, cwd: Path, env: Mapping[str, str], timeout: float
    ) -> CommandResult:
        return CommandResult(
            returncode=None, stdout="", stderr="", duration_s=0.0, timed_out=False, cancelled=True
        )


def test_cancelled_run_is_skipped(tmp_path: Path) -> None:
    [row] = RunInRepos(CancelledRunner({}), parallel=1, timeout=5, fail_fast=False)(
        _targets(tmp_path, "api"), ["x"]
    )
    assert (row.action, row.returncode, row.detail, row.error) == (
        "skipped",
        None,
        "cancelled",
        None,
    )


def test_missing_dir_fails_without_running(tmp_path: Path) -> None:
    [target] = _targets(tmp_path, "api")
    target.path.rmdir()
    runner = FakeRunner({})
    [row] = RunInRepos(runner, parallel=1, timeout=5, fail_fast=False)([target], ["x"])
    assert (row.action, row.detail, runner.calls) == ("failed", "missing", [])


def test_on_done_streams_each_row(tmp_path: Path) -> None:
    seen: list[str] = []
    RunInRepos(
        FakeRunner({}),
        parallel=2,
        timeout=5,
        fail_fast=False,
        on_done=lambda r: seen.append(r.repo),
    )(_targets(tmp_path, "api", "web"), ["x"])
    assert sorted(seen) == ["acme/api", "acme/web"]


class SlowRunner:
    """Counts concurrent calls; ``slow`` names sleep longer."""

    def __init__(self, codes: Mapping[str, int | None] | None = None, slow: str = "") -> None:
        self.codes = codes or {}
        self.slow = slow
        self.active = 0
        self.max_active = 0
        self.started: list[str] = []
        self.lock = threading.Lock()
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True

    def run(
        self, argv: Sequence[str], *, cwd: Path, env: Mapping[str, str], timeout: float
    ) -> CommandResult:
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.started.append(cwd.name)
        time.sleep(0.3 if cwd.name == self.slow else 0.05)
        with self.lock:
            self.active -= 1
        return CommandResult(
            returncode=self.codes.get(cwd.name, 0),
            stdout="",
            stderr="",
            duration_s=0.05,
            timed_out=False,
        )


def test_parallelism_is_bounded(tmp_path: Path) -> None:
    runner = SlowRunner()
    RunInRepos(runner, parallel=2, timeout=5, fail_fast=False)(
        _targets(tmp_path, "a", "b", "c", "d", "e"), ["x"]
    )
    assert runner.max_active == 2


def test_fail_fast_with_parallel_lets_running_jobs_finish(tmp_path: Path) -> None:
    runner = SlowRunner({"a": 1}, slow="b")
    rows = RunInRepos(runner, parallel=2, timeout=5, fail_fast=True)(
        _targets(tmp_path, "a", "b", "c", "d"), ["x"]
    )
    assert [r.action for r in rows] == ["failed", "ran", "skipped", "skipped"]
    assert sorted(runner.started) == ["a", "b"]


def test_rows_keep_target_order(tmp_path: Path) -> None:
    rows = RunInRepos(SlowRunner(slow="a"), parallel=2, timeout=5, fail_fast=False)(
        _targets(tmp_path, "a", "b"), ["x"]
    )
    assert [r.repo for r in rows] == ["acme/a", "acme/b"]


def test_on_done_called_once_per_row(tmp_path: Path) -> None:
    seen: list[str] = []
    RunInRepos(
        SlowRunner({"a": 1}),
        parallel=2,
        timeout=5,
        fail_fast=True,
        on_done=lambda r: seen.append(r.repo),
    )(_targets(tmp_path, "a", "b", "c"), ["x"])
    assert sorted(seen) == ["acme/a", "acme/b", "acme/c"]


def test_interrupt_cancels_the_runner_and_propagates(tmp_path: Path) -> None:
    class Interrupting(FakeRunner):
        def run(
            self, argv: Sequence[str], *, cwd: Path, env: Mapping[str, str], timeout: float
        ) -> CommandResult:
            raise KeyboardInterrupt

    runner = Interrupting({})
    with pytest.raises(KeyboardInterrupt):
        RunInRepos(runner, parallel=2, timeout=5, fail_fast=False)(
            _targets(tmp_path, "a", "b"), ["x"]
        )
    assert runner.cancelled
