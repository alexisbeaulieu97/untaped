"""SubprocessRunner with real processes."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from threading import Thread

import pytest

from untaped_workspace.domain.models import CommandResult
from untaped_workspace.infrastructure import SubprocessRunner


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


def _assert_stops(pid: int, limit: float = 10.0) -> None:
    deadline = time.monotonic() + limit
    while not _is_dead(pid):
        if time.monotonic() > deadline:
            os.kill(pid, 9)  # do not leak the sleeper past the test
            pytest.fail(f"background process {pid} survived the run")
        time.sleep(0.05)


def test_timeout_stops_the_process_tree_and_keeps_partial_output(tmp_path: Path) -> None:
    result = SubprocessRunner().run(
        # PID is staged then renamed, so the timeout never leaves it half-written.
        ["sh", "-c", "sleep 60 & echo partial; echo $! > PID.tmp && mv PID.tmp PID; wait"],
        cwd=tmp_path,
        env={},
        timeout=0.5,
    )
    assert (result.timed_out, result.returncode) == (True, None)
    # On a very slow host the shell may be stopped before it gets that far.
    if (tmp_path / "PID").exists():
        assert "partial" in result.stdout
        _assert_stops(int((tmp_path / "PID").read_text()))


def test_a_timeout_sends_sigterm_first(tmp_path: Path) -> None:
    # A command that cleans up on SIGTERM gets to (before, the timeout was SIGKILL at once).
    script = "trap 'touch CLEANED; exit 1' TERM; touch STARTED; while :; do sleep 0.05; done"
    result = SubprocessRunner().run(["sh", "-c", script], cwd=tmp_path, env={}, timeout=1)
    assert (result.timed_out, result.returncode) == (True, None)
    if (tmp_path / "STARTED").exists():  # else the timeout came before the trap was set
        assert (tmp_path / "CLEANED").exists()


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
    assert [(r.stopped, r.returncode) for r in results] == [(True, None)] * 2
    time.sleep(0.2)
    assert not list(tmp_path.glob("MARKER_*"))


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
    assert results[0].stopped


def test_nothing_starts_after_cancel(tmp_path: Path) -> None:
    runner = SubprocessRunner()
    runner.cancel()
    result = runner.run(["sh", "-c", "touch MARKER"], cwd=tmp_path, env={}, timeout=5)
    assert (result.returncode, result.timed_out, result.cancelled) == (None, False, True)
    assert not (tmp_path / "MARKER").exists()


def test_a_background_process_holding_the_output_is_stopped_after_the_command_exits(
    tmp_path: Path,
) -> None:
    started = time.monotonic()
    result = SubprocessRunner().run(
        ["sh", "-c", "sleep 60 & echo $!; exit 3"], cwd=tmp_path, env={}, timeout=60
    )
    assert (result.returncode, result.timed_out, result.stopped) == (3, False, False)
    assert time.monotonic() - started < 10  # about two seconds, not the timeout
    _assert_stops(int(result.stdout.strip()))


def test_a_background_process_that_closed_the_output_keeps_running(tmp_path: Path) -> None:
    # A daemon a command starts (a build server, a cache) outlives it, as after `cmd &`.
    result = SubprocessRunner().run(
        ["sh", "-c", "sleep 60 >/dev/null 2>&1 & echo $!"], cwd=tmp_path, env={}, timeout=30
    )
    assert (result.returncode, result.timed_out) == (0, False)
    pid = int(result.stdout.strip())
    try:
        time.sleep(0.3)
        assert not _is_dead(pid)
    finally:
        os.kill(pid, signal.SIGKILL)


_UNTAPED_RUN = """
import sys
from pathlib import Path
from untaped.sdk import _process_groups  # what untaped's entry point installs
from untaped_workspace.application.run import RunInRepos, RunTarget
from untaped_workspace.domain.models import RepoSpec
from untaped_workspace.infrastructure import SubprocessRunner
_process_groups.install()
root = Path(sys.argv[1])
targets = []
for i in range(6):
    (root / str(i)).mkdir()
    spec = RepoSpec(url=f"https://git.example/acme/{i}.git", name=f"acme/{i}", dir=str(i),
                    branch="J-1", base="main")
    targets.append(RunTarget(workspace="J-1", spec=spec, path=root / str(i)))
script = "trap '' INT; echo $$ > ../$(basename $PWD).pid; sleep 30"  # ignores Ctrl-C
run = RunInRepos(SubprocessRunner(), parallel=2, timeout=60, fail_fast=False)
print("ready", flush=True)
for row in run(targets, ["sh", "-c", script]):
    print(row.action, flush=True)
"""


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups (#1013)")
@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM], ids=["sigint", "sigterm"])
def test_a_signal_to_untaped_stops_every_command_and_starts_no_more(
    tmp_path: Path, sig: signal.Signals
) -> None:
    root = tmp_path / "repos"
    root.mkdir()
    untaped = subprocess.Popen(
        [sys.executable, "-c", _UNTAPED_RUN, str(root)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert untaped.stdout is not None
        assert untaped.stdout.readline() == "ready\n"
        _wait_for([root / "0.pid", root / "1.pid"])
        untaped.send_signal(sig)
        _, err = untaped.communicate(timeout=15)
    finally:
        untaped.kill()
    assert untaped.returncode == -sig, err  # the interrupt ends it; SIGTERM by the signal
    pids = sorted(root.glob("*.pid"))
    assert [p.name for p in pids] == ["0.pid", "1.pid"]  # nothing started after the signal
    for pid_file in pids:
        _assert_stops(int(pid_file.read_text()), limit=5)
