# tests/workspace/integration/test_command_runner.py
"""SubprocessRunner with real processes."""

from __future__ import annotations

import os
import time
from pathlib import Path
from threading import Thread, current_thread, main_thread
from types import SimpleNamespace

import pytest

from untaped_workspace.domain import CommandResult
from untaped_workspace.infrastructure import SubprocessRunner, command_runner


def test_captures_output_and_code(tmp_path: Path) -> None:
    result = SubprocessRunner().run(
        ["sh", "-c", "echo hi; echo err >&2; exit 3"], cwd=tmp_path, env={}, timeout=10
    )
    assert (result.returncode, result.stdout, result.stderr, result.timed_out) == (
        3,
        "hi\n",
        "err\n",
        False,
    )


def test_env_and_cwd(tmp_path: Path) -> None:
    result = SubprocessRunner().run(
        ["sh", "-c", 'echo "$UNTAPED_REPO $(basename "$PWD")"'],
        cwd=tmp_path,
        env={"UNTAPED_REPO": "acme/api"},
        timeout=10,
    )
    assert result.stdout.strip() == f"acme/api {tmp_path.name}"


def test_timeout(tmp_path: Path) -> None:
    result = SubprocessRunner().run(["sh", "-c", "sleep 5"], cwd=tmp_path, env={}, timeout=0.2)
    assert (result.timed_out, result.returncode) == (True, None)


def test_undecodable_output_is_replaced(tmp_path: Path) -> None:
    result = SubprocessRunner().run(
        ["sh", "-c", "printf '\\377ok'"], cwd=tmp_path, env={}, timeout=10
    )
    assert result.stdout.endswith("ok")


def test_unrunnable_file_is_127(tmp_path: Path) -> None:
    script = tmp_path / "s.sh"
    script.write_text("echo hi")
    result = SubprocessRunner().run(
        [str(script)], cwd=tmp_path, env={}, timeout=10
    )  # not executable
    assert result.returncode == 127


def test_timeout_kills_the_process_tree_and_keeps_partial_output(tmp_path: Path) -> None:
    result = SubprocessRunner().run(
        ["sh", "-c", "(touch STARTED; sleep 3; touch MARKER) & echo partial; wait"],
        cwd=tmp_path,
        env={},
        timeout=0.5,
    )
    assert (result.timed_out, result.returncode) == (True, None)
    # On a very slow host the shell may be killed before it gets to echo.
    if (tmp_path / "STARTED").exists():
        assert "partial" in result.stdout
    time.sleep(4)  # past the grandchild's sleep 3: it would have written MARKER by now
    assert not (tmp_path / "MARKER").exists()


def test_bad_input_is_127_not_an_exception(tmp_path: Path) -> None:
    runner = SubprocessRunner()
    assert runner.run(["a\0b"], cwd=tmp_path, env={}, timeout=5).returncode == 127
    assert runner.run([], cwd=tmp_path, env={}, timeout=5).returncode == 127


def _wait_for(paths: list[Path], limit: float = 10.0) -> None:
    deadline = time.monotonic() + limit
    while not all(p.exists() for p in paths):
        assert time.monotonic() < deadline, "commands did not start"
        time.sleep(0.02)


def _start(
    runner: SubprocessRunner, script: str, cwd: Path, results: list[CommandResult]
) -> Thread:
    thread = Thread(
        target=lambda: results.append(runner.run(["sh", "-c", script], cwd=cwd, env={}, timeout=60))
    )
    thread.start()
    return thread


def test_cancel_stops_running_commands(tmp_path: Path) -> None:
    runner = SubprocessRunner()
    results: list[CommandResult] = []
    threads = [
        _start(runner, f"touch STARTED_{i}; sleep 5; touch MARKER_{i}", tmp_path, results)
        for i in range(2)
    ]
    _wait_for([tmp_path / "STARTED_0", tmp_path / "STARTED_1"])
    started = time.monotonic()
    runner.cancel()
    for t in threads:
        t.join(timeout=8)
    assert time.monotonic() - started < 4
    assert len(results) == 2
    time.sleep(0.2)
    assert not list(tmp_path.glob("MARKER_*"))
    assert runner.active_count() == 0


def test_cancel_kills_a_command_that_ignores_term(tmp_path: Path) -> None:
    runner = SubprocessRunner()
    results: list[CommandResult] = []
    thread = _start(runner, "trap '' TERM; touch STARTED; sleep 30", tmp_path, results)
    _wait_for([tmp_path / "STARTED"])
    started = time.monotonic()
    runner.cancel()
    thread.join(timeout=8)
    assert not thread.is_alive()
    assert time.monotonic() - started < 4
    assert runner.active_count() == 0


def test_nothing_starts_after_cancel(tmp_path: Path) -> None:
    runner = SubprocessRunner()
    runner.cancel()
    result = runner.run(["sh", "-c", "touch MARKER"], cwd=tmp_path, env={}, timeout=5)
    assert (result.returncode, result.timed_out, result.cancelled) == (None, False, True)
    assert not (tmp_path / "MARKER").exists()


def test_tracking_is_empty_after_normal_and_timeout_runs(tmp_path: Path) -> None:
    runner = SubprocessRunner()
    runner.run(["true"], cwd=tmp_path, env={}, timeout=5)
    assert runner.active_count() == 0
    runner.run(["sh", "-c", "sleep 5"], cwd=tmp_path, env={}, timeout=0.2)
    assert runner.active_count() == 0


def test_second_interrupt_during_cancel_still_kills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = SubprocessRunner()
    results: list[CommandResult] = []
    thread = _start(runner, "trap '' TERM; touch STARTED; sleep 30", tmp_path, results)
    _wait_for([tmp_path / "STARTED"])

    real_sleep = time.sleep

    def interrupted(seconds: float) -> None:
        if current_thread() is not main_thread():  # the run's own waits are not interrupted
            real_sleep(seconds)
            return
        raise KeyboardInterrupt

    monkeypatch.setattr(
        command_runner, "time", SimpleNamespace(monotonic=time.monotonic, sleep=interrupted)
    )
    with pytest.raises(KeyboardInterrupt):
        runner.cancel()
    thread.join(timeout=15)  # the command ignores TERM and would sleep 30s
    assert not thread.is_alive()


def test_background_child_is_stopped_after_the_leader_exits(tmp_path: Path) -> None:
    started = time.monotonic()
    result = SubprocessRunner().run(
        ["sh", "-c", "(sleep 6; touch MARKER) & echo started; exit 3"],
        cwd=tmp_path,
        env={},
        timeout=60,
    )
    assert (result.returncode, result.timed_out) == (3, False)
    assert "started" in result.stdout
    # Past the background sleep: it would have written MARKER by now.
    time.sleep(max(0.0, started + 7 - time.monotonic()))
    assert not (tmp_path / "MARKER").exists()


def _is_dead(pid: int) -> bool:
    """Gone, or a zombie waiting for a reaper (it no longer runs)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    stat = Path(f"/proc/{pid}/stat")
    try:
        return stat.read_text().rsplit(")", 1)[1].split()[0] == "Z"
    except OSError, IndexError:
        return not stat.parent.exists() and Path("/proc/self").exists()


def test_background_process_that_closed_the_pipes_is_stopped(tmp_path: Path) -> None:
    result = SubprocessRunner().run(
        ["sh", "-c", "sleep 60 >/dev/null 2>&1 & echo $!"], cwd=tmp_path, env={}, timeout=30
    )
    assert (result.returncode, result.timed_out) == (0, False)
    pid = int(result.stdout.strip())
    deadline = time.monotonic() + 10
    while not _is_dead(pid):
        if time.monotonic() > deadline:
            os.kill(pid, 9)  # do not leak the sleeper past the test
            pytest.fail(f"background process {pid} survived the run")
        time.sleep(0.05)
