"""Unit tests for :mod:`untaped.capabilities.workspace.infrastructure.system_adapters`.

Editor-resolution lives here so the application layer can stay pure: it
takes a fully-resolved ``argv`` and never sees ``os.environ`` or
``os.name``. Both POSIX and Windows splitting branches are exercised
via the ``posix=`` knob, so the test suite doesn't depend on the CI
runner's OS.
"""

from __future__ import annotations

import os
import shlex
import signal
import sys
import time
from pathlib import Path

import pytest

from untaped.capabilities.workspace.errors import WorkspaceError
from untaped.capabilities.workspace.infrastructure.system_adapters import (
    DEFAULT_FOREACH_TIMEOUT,
    InterruptibleShellRunner,
    resolve_editor_argv,
    shell_runner,
)

# -- shell runner ------------------------------------------------------------


def test_default_foreach_timeout_matches_documented_default() -> None:
    assert DEFAULT_FOREACH_TIMEOUT == 600.0


def test_shell_runner_closes_stdin(tmp_path: Path) -> None:
    script = "import sys; print('eof' if sys.stdin.read() == '' else 'open')"

    result = shell_runner(
        f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}",
        tmp_path,
        timeout=2.0,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "eof"


def test_shell_runner_timeout_returns_failed_completed_process(tmp_path: Path) -> None:
    script = "import time; print('started', flush=True); time.sleep(60)"

    result = shell_runner(
        f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}",
        tmp_path,
        timeout=0.1,
    )

    assert result.returncode == 124
    assert result.stdout.strip() == "started"
    assert "timed out after 0.1s" in result.stderr


@pytest.mark.skipif(os.name == "nt", reason="process group cleanup is POSIX-only")
def test_shell_runner_timeout_kills_background_child_process(tmp_path: Path) -> None:
    pidfile = tmp_path / "child.pid"
    script = (
        "import os, pathlib, time; "
        f"pathlib.Path({str(pidfile)!r}).write_text(str(os.getpid())); "
        "time.sleep(60)"
    )
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(script)} & wait"

    result = shell_runner(command, tmp_path, timeout=0.2)

    assert result.returncode == 124
    deadline = time.monotonic() + 2.0
    while not pidfile.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert pidfile.exists()

    child_pid = int(pidfile.read_text())
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        os.kill(child_pid, signal.SIGKILL)
        pytest.fail(f"timed-out child process {child_pid} was not reaped")


@pytest.mark.skipif(os.name == "nt", reason="process group cleanup is POSIX-only")
def test_shell_runner_interrupt_kills_process_group_and_reraises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    pidfile = tmp_path / "child.pid"
    script = (
        "import os, pathlib, time; "
        f"pathlib.Path({str(pidfile)!r}).write_text(str(os.getpid())); "
        "time.sleep(60)"
    )
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(script)} & wait"
    original = subprocess.Popen.communicate
    spawned: list[subprocess.Popen[str]] = []

    def interrupted(self: subprocess.Popen[str], *args: object, **kwargs: object) -> object:
        if not spawned:
            spawned.append(self)
            deadline = time.monotonic() + 5.0
            while not pidfile.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            raise KeyboardInterrupt
        return original(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(subprocess.Popen, "communicate", interrupted)

    with pytest.raises(KeyboardInterrupt):
        shell_runner(command, tmp_path, timeout=30)

    assert spawned[0].returncode is not None  # shell reaped
    child_pid = int(pidfile.read_text())
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        if _is_zombie(child_pid):
            break
        time.sleep(0.05)
    else:
        os.kill(child_pid, signal.SIGKILL)
        pytest.fail(f"interrupted child process {child_pid} was left running")


@pytest.mark.skipif(os.name == "nt", reason="process group cleanup is POSIX-only")
def test_interruptible_runner_terminate_all_kills_live_commands_and_refuses_new(
    tmp_path: Path,
) -> None:
    import threading

    runner = InterruptibleShellRunner()
    pidfile = tmp_path / "pid"
    command = f"echo $$ > {pidfile}; sleep 30"
    results: list[int] = []
    worker = threading.Thread(
        target=lambda: results.append(runner(command, tmp_path, timeout=60).returncode)
    )
    worker.start()
    deadline = time.monotonic() + 5.0
    while not (pidfile.exists() and pidfile.read_text().strip()):
        assert time.monotonic() < deadline, "command never started"
        time.sleep(0.01)

    start = time.monotonic()
    runner.terminate_all()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert time.monotonic() - start < 3
    assert results and results[0] != 0
    assert runner("echo late", tmp_path, timeout=60).returncode == 130


def _is_zombie(pid: int) -> bool:
    try:
        stat_line = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    return stat_line.rsplit(")", 1)[-1].split()[0] == "Z"


# ── editor selection precedence ────────────────────────────────────────────


def test_explicit_editor_wins_over_env() -> None:
    argv = resolve_editor_argv("code", env={"VISUAL": "subl", "EDITOR": "vi"})
    assert argv == ("code",)


def test_visual_beats_editor_when_no_explicit() -> None:
    argv = resolve_editor_argv(None, env={"VISUAL": "subl", "EDITOR": "vi"})
    assert argv == ("subl",)


def test_editor_used_when_visual_missing() -> None:
    argv = resolve_editor_argv(None, env={"EDITOR": "nvim"})
    assert argv == ("nvim",)


def test_vi_fallback_when_env_is_empty() -> None:
    argv = resolve_editor_argv(None, env={})
    assert argv == ("vi",)


# ── argument splitting ─────────────────────────────────────────────────────


def test_splits_editor_with_flags() -> None:
    argv = resolve_editor_argv("code --reuse-window", env={})
    assert argv == ("code", "--reuse-window")


def test_preserves_quoted_segments_in_posix_mode() -> None:
    argv = resolve_editor_argv(None, env={"VISUAL": 'sh -c "exec vim $0"'}, posix=True)
    assert argv == ("sh", "-c", "exec vim $0")


def test_preserves_windows_paths_when_posix_false() -> None:
    """``posix=False`` must keep backslashes intact. POSIX mode would
    mangle ``C:\\Tools\\vim.exe`` to ``C:Toolsvim.exe`` before
    subprocess ever sees it — that's the whole reason ``posix`` is a
    knob and not always ``True``."""
    argv = resolve_editor_argv(r"C:\Tools\vim.exe", env={}, posix=False)
    assert argv == (r"C:\Tools\vim.exe",)


# ── error translation ─────────────────────────────────────────────────────


def test_empty_editor_raises_workspace_error() -> None:
    with pytest.raises(WorkspaceError, match="editor command is empty"):
        resolve_editor_argv("   ", env={})


def test_unterminated_quotes_raise_workspace_error_with_message() -> None:
    """``shlex.split`` raises ``ValueError`` on unterminated quoting; the
    helper translates that into a ``WorkspaceError`` so callers see the
    same shape they already handle today (an `UntapedError` subclass).
    Pins the chained-exception contract too so debugging ever-rarer
    shlex edge cases still has the original cause attached."""
    with pytest.raises(WorkspaceError, match="could not parse editor command") as exc_info:
        resolve_editor_argv('sh -c "missing-close', env={}, posix=True)
    assert isinstance(exc_info.value.__cause__, ValueError)
