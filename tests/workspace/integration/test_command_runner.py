# tests/workspace/integration/test_command_runner.py
"""SubprocessRunner with real processes."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from untaped.capabilities.workspace.infrastructure import SubprocessRunner


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
        ["sh", "-c", "echo partial; sleep 1; touch MARKER"], cwd=tmp_path, env={}, timeout=0.2
    )
    time.sleep(1.5)
    assert (result.timed_out, result.returncode) == (True, None)
    assert "partial" in result.stdout
    assert not (tmp_path / "MARKER").exists()


def test_bad_input_is_127_not_an_exception(tmp_path: Path) -> None:
    runner = SubprocessRunner()
    assert runner.run(["a\0b"], cwd=tmp_path, env={}, timeout=5).returncode == 127
    assert runner.run([], cwd=tmp_path, env={}, timeout=5).returncode == 127


def test_cancel_stops_running_commands(tmp_path: Path) -> None:
    runner = SubprocessRunner()
    results = []
    threads = [
        threading.Thread(
            target=lambda i=i: results.append(
                runner.run(
                    ["sh", "-c", f"sleep 5; touch MARKER_{i}"], cwd=tmp_path, env={}, timeout=30
                )
            )
        )
        for i in range(2)
    ]
    for t in threads:
        t.start()
    time.sleep(0.3)
    started = time.monotonic()
    runner.cancel()
    for t in threads:
        t.join(timeout=5)
    assert time.monotonic() - started < 4
    assert len(results) == 2
    time.sleep(0.2)
    assert not list(tmp_path.glob("MARKER_*"))
