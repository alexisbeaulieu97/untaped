# tests/workspace/unit/test_run_use_case.py
"""RunInRepos scheduling, environment, failure rules — with a fake runner."""

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from pathlib import Path

from untaped.capabilities.workspace.application.run import RunInRepos, RunTarget
from untaped.capabilities.workspace.domain import CommandResult, RepoSpec


class FakeRunner:
    def __init__(self, codes: Mapping[str, int | None]) -> None:
        self.codes = codes
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.lock = threading.Lock()

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
