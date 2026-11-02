"""Child processes in their own group (``untaped.sdk.process``) and untaped's own signals.

Real children run with ``sys.executable``. Tests that install the signal
handlers, or send untaped a signal, run untaped's side in a child Python, so
the test process keeps its own handlers.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from untaped.sdk import _process_groups
from untaped.sdk.errors import ErrorCategory, UntapedError
from untaped.sdk.process import ProcessResult, run_process, start_process

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX process groups (#1013)")

GRACE = 0.5


@pytest.fixture(autouse=True)
def short_grace(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(_process_groups, "GRACE_S", GRACE)
    yield
    assert not _process_groups._S.groups, "a test left a group registered"


def _py(code: str) -> list[str]:
    return [sys.executable, "-c", textwrap.dedent(code)]


def _gone(pid: int) -> bool:
    """Whether ``pid`` has exited (a zombie no reaper collected counts as gone)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] == "Z"
    except OSError:
        return False


def _wait_gone(pid: int, seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while not _gone(pid):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.02)
    return True


def _wait_for(path: Path, seconds: float = 10.0) -> None:
    deadline = time.monotonic() + seconds
    while not path.exists():
        assert time.monotonic() < deadline, f"{path.name} never appeared"
        time.sleep(0.02)


def _pid(path: Path) -> int:
    _wait_for(path)
    return int(path.read_text())


def _record_pid(path: Path) -> str:
    """Code that writes the process's pid to ``path`` atomically."""
    return (
        f"import os\nwith open({str(path)!r} + '.tmp', 'w') as f: f.write(str(os.getpid()))\n"
        f"os.replace({str(path)!r} + '.tmp', {str(path)!r})\n"
    )


def _stubborn(pid_file: Path, *, ignore: str = "SIGINT, SIGTERM") -> list[str]:
    """A child that ignores ``ignore`` and starts a grandchild that does too; both pids recorded."""
    grandchild = (
        "import signal, time\n"
        + "".join(f"signal.signal(signal.{s.strip()}, signal.SIG_IGN)\n" for s in ignore.split(","))
        + _record_pid(Path(f"{pid_file}.grandchild"))
        + "time.sleep(60)\n"
    )
    return _py(
        "import signal, subprocess, sys, time\n"
        + "".join(f"signal.signal(signal.{s.strip()}, signal.SIG_IGN)\n" for s in ignore.split(","))
        + f"subprocess.Popen([sys.executable, '-c', {grandchild!r}])\n"
        + _record_pid(pid_file)
        + "time.sleep(60)\n"
    )


# ── run_process ─────────────────────────────────────────────────────────────


def test_a_child_runs_to_the_end_with_its_output(tmp_path: Path) -> None:
    result = run_process(
        _py(
            "import os, sys\nprint(os.getcwd(), os.environ['ONLY'], sys.stdin.read() == '')\n"
            "sys.stderr.write('note')\nsys.exit(3)\n"
        ),
        timeout=10,
        cwd=tmp_path,
        env={"ONLY": "this", "PATH": os.environ["PATH"]},
    )

    assert result.ended == "exited"
    assert result.returncode == 3
    assert result.stdout.decode().split() == [str(tmp_path), "this", "True"]
    assert result.stderr == b"note"
    assert result.duration_s > 0


def test_input_reaches_the_child() -> None:
    result = run_process(
        _py("import sys; sys.stdout.write(sys.stdin.read()[::-1])"), timeout=10, input=b"abc"
    )
    assert result.stdout == b"cba"


def test_a_child_that_dies_of_a_signal_reports_it() -> None:
    result = run_process(_py("import os, signal; os.kill(os.getpid(), signal.SIGUSR1)"), timeout=10)
    assert (result.ended, result.returncode) == ("exited", -signal.SIGUSR1)


def test_no_program_raises_os_error_and_no_argv_value_error(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        run_process([str(tmp_path / "missing")], timeout=5)
    with pytest.raises(ValueError, match="empty"):
        run_process([], timeout=5)


def test_a_timeout_gives_the_group_one_sigterm_then_sigkill(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    started = time.monotonic()

    result = run_process(_stubborn(pid_file), timeout=1)

    assert result.ended == "timed_out"
    assert result.returncode is None
    assert time.monotonic() - started < 1 + 2 * GRACE + 2
    assert _wait_gone(_pid(pid_file)), "the child outlived the timeout"
    assert _wait_gone(_pid(Path(f"{pid_file}.grandchild"))), "the grandchild outlived it"


def test_a_timeout_lets_a_child_that_handles_sigterm_clean_up(tmp_path: Path) -> None:
    marker = tmp_path / "cleaned"
    result = run_process(
        _py(
            "import signal, sys, time\n"
            f"def done(*_):\n    open({str(marker)!r}, 'w').close()\n    sys.exit(143)\n"
            "signal.signal(signal.SIGTERM, done)\n"
            "print('ready', flush=True)\ntime.sleep(60)\n"
        ),
        timeout=1,
    )
    assert result.ended == "timed_out"
    assert marker.exists()
    assert result.stdout == b"ready\n"


def _leaves(pid_file: Path, *, holds_output: bool) -> list[str]:
    """A child that starts a sleeper (holding its output or not), waits for it, and exits."""
    sleeper = _record_pid(pid_file) + "import time; time.sleep(60)"
    quiet = "" if holds_output else ", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL"
    return _py(
        "import os, subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', {sleeper!r}]{quiet})\n"
        f"while not os.path.exists({str(pid_file)!r}): time.sleep(0.01)\n"
        "print('left')\n"
    )


def test_what_a_finished_child_left_runs_on(tmp_path: Path) -> None:
    pid_file = tmp_path / "sleeper.pid"
    result = run_process(_leaves(pid_file, holds_output=False), timeout=30)
    try:
        assert (result.ended, result.returncode, result.stdout) == ("exited", 0, b"left\n")
        assert not _gone(_pid(pid_file)), "a leftover holding nothing was stopped (as `cmd &`)"
    finally:
        os.kill(_pid(pid_file), signal.SIGKILL)


def test_a_leftover_holding_the_output_is_stopped_one_grace_after_the_child_exits(
    tmp_path: Path,
) -> None:
    pid_file = tmp_path / "sleeper.pid"
    started = time.monotonic()

    result = run_process(_leaves(pid_file, holds_output=True), timeout=30)

    assert (result.ended, result.returncode, result.stdout) == ("exited", 0, b"left\n")
    assert time.monotonic() - started < 2 * GRACE + 3, "the run waited past the grace"
    assert _wait_gone(_pid(pid_file)), "the leftover holding the output was not stopped"


def test_stop_ends_a_run_and_a_set_stop_starts_nothing(tmp_path: Path) -> None:
    stop = threading.Event()
    pid_file = tmp_path / "child.pid"
    threading.Thread(target=lambda: (_wait_for(pid_file), stop.set()), daemon=True).start()

    result = run_process(_stubborn(pid_file, ignore="SIGINT"), timeout=30, stop=stop)

    assert (result.ended, result.returncode) == ("stopped", None)
    assert _wait_gone(_pid(tmp_path / "child.pid"))
    marker = tmp_path / "never"
    again = run_process(_py(f"open({str(marker)!r}, 'w')"), timeout=5, stop=stop)
    assert again.ended == "stopped"
    assert not marker.exists()


@pytest.mark.skipif(
    threading.current_thread() is not threading.main_thread(),
    reason="signals reach the main thread",
)
def test_an_exception_in_the_calling_thread_stops_the_group(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"

    def interrupt(*_: object) -> None:
        if pid_file.exists() and Path(f"{pid_file}.grandchild").exists():
            raise KeyboardInterrupt
        signal.setitimer(signal.ITIMER_REAL, 0.05)

    previous = signal.signal(signal.SIGALRM, interrupt)
    try:
        signal.setitimer(signal.ITIMER_REAL, 0.05)
        with pytest.raises(KeyboardInterrupt):
            run_process(_stubborn(pid_file), timeout=60)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)

    assert _wait_gone(_pid(pid_file))
    assert _wait_gone(_pid(Path(f"{pid_file}.grandchild")))


def test_a_run_returns_in_bounded_time_against_an_escapee_holding_the_output(
    tmp_path: Path,
) -> None:
    escapee = tmp_path / "escapee.pid"
    sleeper = _record_pid(escapee) + "import time; time.sleep(60)"
    code = (
        "import os, subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', {sleeper!r}], start_new_session=True)\n"
        f"while not os.path.exists({str(escapee)!r}): time.sleep(0.01)\n"
        "time.sleep(60)\n"
    )
    started = time.monotonic()
    try:
        result = run_process(_py(code), timeout=1)
        assert result.ended == "timed_out"
        assert time.monotonic() - started < 1 + 2 * GRACE + 2
    finally:
        os.kill(_pid(escapee), signal.SIGKILL)


# ── start_process ───────────────────────────────────────────────────────────


def test_a_long_lived_child_answers_and_close_ends_it() -> None:
    with start_process(
        _py("import sys\nfor line in sys.stdin: print(line.upper(), end='', flush=True)")
    ) as child:
        child.stdin.write(b"hello\n")
        child.stdin.flush()
        assert child.stdout.readline() == b"HELLO\n"
        assert child.poll() is None
    assert child.close() == 0  # closing stdin ended it; idempotent
    child.stdout.close()
    child.stderr.close()


def test_close_stops_a_child_that_ignores_end_of_file_from_two_threads(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    child = start_process(_stubborn(pid_file, ignore="SIGINT"))
    _pid(pid_file)
    codes: list[int] = []
    closers = [threading.Thread(target=lambda: codes.append(child.close())) for _ in range(2)]
    for closer in closers:
        closer.start()
    for closer in closers:
        closer.join(timeout=10)

    assert codes == [-signal.SIGTERM, -signal.SIGTERM]
    assert _wait_gone(_pid(Path(f"{pid_file}.grandchild")))
    child.stdout.close()
    child.stderr.close()


def test_close_still_stops_the_group_when_its_grace_is_interrupted(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    child = start_process(_stubborn(pid_file, ignore="SIGINT"))
    grandchild = _pid(Path(f"{pid_file}.grandchild"))

    real_wait = child._process.wait

    def interrupted(timeout: float | None = None) -> int:
        child._process.wait = real_wait  # type: ignore[method-assign]
        raise KeyboardInterrupt

    child._process.wait = interrupted  # type: ignore[method-assign]  # Ctrl-C mid-grace
    with pytest.raises(KeyboardInterrupt):
        child.close()
    assert not _process_groups._S.groups
    assert _wait_gone(_pid(pid_file)) and _wait_gone(grandchild)
    assert child.close() == -signal.SIGTERM  # a second close returns what the first saw
    child.stdout.close()
    child.stderr.close()


def test_without_process_groups_the_leader_is_stopped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(_process_groups, "HAS_PROCESS_GROUPS", False)  # as on Windows (#1013)
    pid_file = tmp_path / "child.pid"
    result = run_process(_py(_record_pid(pid_file) + "import time; time.sleep(30)"), timeout=0.3)
    assert (result.ended, result.returncode) == ("timed_out", None)
    assert _wait_gone(_pid(pid_file))


def test_a_child_that_exits_on_its_own_is_deregistered_when_seen() -> None:
    child = start_process(_py("pass"))
    assert child.wait(timeout=10) == 0
    assert not _process_groups._S.groups
    child.close()
    child.stdout.close()
    child.stderr.close()


def test_a_child_that_exits_of_a_forwarded_signal_was_stopped() -> None:
    results: list[ProcessResult] = []
    worker = threading.Thread(
        target=lambda: results.append(run_process(_py("import time; time.sleep(30)"), timeout=30))
    )
    worker.start()
    deadline = time.monotonic() + 10
    while not _process_groups._S.groups:
        assert time.monotonic() < deadline, "the child never started"
        time.sleep(0.02)
    _process_groups.ending(signal.SIGTERM)  # what the handler forwards, short of raising
    worker.join(timeout=10)
    assert (results[0].ended, results[0].returncode) == ("stopped", None)


def test_once_closing_a_worker_starts_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_process_groups._S, "closing", (signal.SIGTERM, time.monotonic()))
    outcomes: list[object] = []

    def work() -> None:
        outcomes.append(run_process(["true"], timeout=5).ended)
        try:
            start_process(["true"])
        except UntapedError as error:
            outcomes.append(error.category)

    worker = threading.Thread(target=work)
    worker.start()
    worker.join()
    assert outcomes == ["stopped", ErrorCategory.INTERRUPTED]
    with pytest.raises(ValueError, match="argv is empty"):
        start_process([])


# ── untaped's own signals (in a child Python) ───────────────────────────────

_UNTAPED = """
import os, signal, sys, threading, time
from untaped.sdk import _process_groups
from untaped.sdk.process import run_process
_process_groups.GRACE_S = {grace}
_process_groups.install()
results = []
def work():
    results.append(run_process({argv!r}, timeout=60).ended)
    print('worker', results[-1], flush=True)
def wait_for(worker):  # short joins: a long one can hold a signal's handler back
    while worker.is_alive():
        worker.join(0.05)
worker = threading.Thread(target=work)
try:
    worker.start()
    print('ready', flush=True)
    wait_for(worker)
except KeyboardInterrupt:
    print('interrupted', _process_groups._S.closing[0], flush=True)
    wait_for(worker)
    print('joined', flush=True)
    raise SystemExit(130)
"""


def _untaped(argv: list[str], *, grace: float = GRACE) -> subprocess.Popen[str]:
    """untaped running ``argv`` on a worker thread, once it is ready for a signal."""
    code = _UNTAPED.format(grace=grace, argv=argv)
    untaped = subprocess.Popen(
        [sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    assert untaped.stdout is not None
    if untaped.stdout.readline() != "ready\n":
        pytest.fail(untaped.communicate(timeout=5)[1])
    return untaped


@pytest.mark.parametrize(
    ("sig", "status"),
    [(signal.SIGINT, 130), (signal.SIGTERM, -signal.SIGTERM), (signal.SIGHUP, -signal.SIGHUP)],
    ids=["sigint", "sigterm", "sighup"],
)
def test_a_signal_to_untaped_stops_a_worker_child_that_ignores_it(
    tmp_path: Path, sig: signal.Signals, status: int
) -> None:
    pid_file = tmp_path / "child.pid"
    untaped = _untaped(_stubborn(pid_file, ignore="SIGINT, SIGTERM, SIGHUP"))
    try:
        child = _pid(pid_file)
        untaped.send_signal(sig)
        out, err = untaped.communicate(timeout=15)
    finally:
        untaped.kill()
    assert untaped.returncode == status, err
    assert out.splitlines() == [f"interrupted {int(sig)}", "worker stopped", "joined"]
    assert _wait_gone(child) and _wait_gone(_pid(Path(f"{pid_file}.grandchild")))


def test_two_sighups_close_together_are_one_event(tmp_path: Path) -> None:
    # A closing terminal sends two; the second must not kill without a grace.
    marker = tmp_path / "cleaned"
    argv = _py(
        "import signal, sys, time\n"
        f"def done(*_):\n    time.sleep({GRACE / 2})\n"  # well inside the 2 s grace below
        f"    open({str(marker)!r}, 'w').close()\n    sys.exit(1)\n"
        "signal.signal(signal.SIGHUP, done)\n"
        + _record_pid(tmp_path / "child.pid")
        + "time.sleep(60)\n"
    )
    untaped = _untaped(argv, grace=2.0)
    try:
        _pid(tmp_path / "child.pid")
        untaped.send_signal(signal.SIGHUP)
        untaped.send_signal(signal.SIGHUP)
        _, err = untaped.communicate(timeout=15)
    finally:
        untaped.kill()
    assert untaped.returncode == -signal.SIGHUP, err
    assert marker.exists(), "the child got SIGKILL instead of its grace"


def test_a_sigint_after_sigterm_still_ends_untaped_by_sigterm(tmp_path: Path) -> None:
    # The SIGTERM already chose how untaped ends; the child that ignores it is still gone.
    pid_file = tmp_path / "child.pid"
    untaped = _untaped(_stubborn(pid_file, ignore="SIGTERM"))
    try:
        child = _pid(pid_file)
        untaped.send_signal(signal.SIGTERM)
        time.sleep(0.3)
        untaped.send_signal(signal.SIGINT)
        _, err = untaped.communicate(timeout=15)
    finally:
        untaped.kill()
    assert untaped.returncode == -signal.SIGTERM, err
    assert _wait_gone(child) and _wait_gone(_pid(Path(f"{pid_file}.grandchild")))


@pytest.mark.parametrize(
    "cleanup",
    ["raise SystemExit(1)", "os.close(1)\n    print('cleaning up', flush=True)"],
    ids=["counted-failure", "cleanup-output-fails"],
)
def test_untaped_still_ends_by_sigterm_when_its_cleanup_fails(tmp_path: Path, cleanup: str) -> None:
    pid_file = tmp_path / "child.pid"
    code = (
        "import os\nfrom untaped.sdk import _process_groups\n"
        "from untaped.sdk.process import run_process\n"
        f"_process_groups.GRACE_S = {GRACE}\n_process_groups.install()\n"
        "try:\n"
        f"    run_process({_stubborn(pid_file, ignore='SIGINT')!r}, timeout=60)\n"
        f"except KeyboardInterrupt:\n    {cleanup}\n"
    )
    untaped = subprocess.Popen(
        [sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    try:
        _pid(pid_file)
        untaped.send_signal(signal.SIGTERM)
        _, err = untaped.communicate(timeout=15)
    finally:
        untaped.kill()
    assert untaped.returncode == -signal.SIGTERM, err
    assert _wait_gone(_pid(pid_file))


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGHUP], ids=["sigterm", "sighup"])
def test_no_credential_file_is_left_after_a_signal_in_the_middle_of_git(
    tmp_path: Path, sig: signal.Signals
) -> None:
    # SIGHUP is what a closing terminal sends untaped.
    pid_file, temp = tmp_path / "git.pid", tmp_path / "tmp"
    temp.mkdir()
    fake_git = tmp_path / "git"
    fake_git.write_text(
        f"#!{sys.executable}\nimport sys, time\n"
        "if sys.argv[1] == 'config':\n    sys.exit(1)\n"  # the SSH config probe
        + _record_pid(pid_file)
        + "time.sleep(60)\n"
    )
    fake_git.chmod(0o755)
    code = (
        "from untaped.sdk import _process_groups\nfrom untaped.sdk.git import run_git\n"
        f"_process_groups.GRACE_S = {GRACE}\n_process_groups.install()\n"
        f"run_git(['fetch'], timeout=60, git={str(fake_git)!r},\n"
        "        auth_config={'http.extraHeader': 'Authorization: Bearer secret'})\n"
    )
    untaped = subprocess.Popen(
        [sys.executable, "-c", code],
        stderr=subprocess.PIPE,
        env={**os.environ, "TMPDIR": str(temp)},
    )
    try:
        _pid(pid_file)
        assert [path.name[:17] for path in temp.iterdir()] == ["untaped-git-auth-"]
        untaped.send_signal(sig)
        _, err = untaped.communicate(timeout=15)
    finally:
        untaped.kill()
    assert untaped.returncode == -sig, err
    assert list(temp.iterdir()) == []


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="PR_SET_CHILD_SUBREAPER")
def test_a_subreaper_does_not_wait_a_grace_for_a_zombie_leftover(tmp_path: Path) -> None:
    # Orphans of the group become untaped's children; their zombies must be reaped, or the
    # group never looks empty and close() waits out the grace and SIGKILLs.
    code = (
        "import ctypes, sys, time\nfrom untaped.sdk import _process_groups\n"
        "from untaped.sdk.process import start_process\n"
        "_process_groups.GRACE_S = 5.0\n"
        "assert ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) == 0\n"
        "child = start_process(['sh', '-c', '(sleep 0.2 >/dev/null 2>&1 </dev/null &)'])\n"
        "started = time.monotonic()\n"
        "print(child.close(), round(time.monotonic() - started) < 3)\n"
        "child.stdout.close(); child.stderr.close()\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=30
    )
    assert result.stdout.split() == ["0", "True"], result.stderr


def test_a_later_sigterm_ends_untaped_at_once(tmp_path: Path) -> None:
    # Cleanup that never ends holds untaped after Ctrl-C: a SIGTERM a grace later ends it now.
    pid_file = tmp_path / "child.pid"
    code = (
        "import time\nfrom untaped.sdk import _process_groups\n"
        "from untaped.sdk.process import run_process\n"
        f"_process_groups.GRACE_S = {GRACE}\n_process_groups.install()\n"
        "try:\n"
        f"    run_process({_stubborn(pid_file, ignore='SIGTERM')!r}, timeout=60)\n"
        "finally:\n    time.sleep(60)\n"
    )
    untaped = subprocess.Popen([sys.executable, "-c", code], stderr=subprocess.DEVNULL)
    try:
        _pid(pid_file)
        untaped.send_signal(signal.SIGINT)
        time.sleep(GRACE * 3)
        assert untaped.returncode is None and untaped.poll() is None
        untaped.send_signal(signal.SIGTERM)
        assert untaped.wait(timeout=5) == -signal.SIGTERM
    finally:
        untaped.kill()
    assert _wait_gone(_pid(pid_file))


def test_an_ignored_signal_stays_ignored_and_an_earlier_handler_runs(tmp_path: Path) -> None:
    code = (
        "import signal\nfrom untaped.sdk import _process_groups\n"
        "signal.signal(signal.SIGHUP, signal.SIG_IGN)\n"
        "seen = []\nsignal.signal(signal.SIGUSR1, signal.SIG_DFL)\n"
        "signal.signal(signal.SIGTERM, lambda *_: seen.append('earlier'))\n"
        "_process_groups.install()\n"
        "assert signal.getsignal(signal.SIGHUP) == signal.SIG_IGN\n"
        "signal.raise_signal(signal.SIGTERM)\n"
        "print(seen)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "['earlier']"


def test_a_worker_starts_no_child_once_untaped_is_closing(tmp_path: Path) -> None:
    code = (
        "import signal, threading\nfrom untaped.sdk import _process_groups\n"
        "from untaped.sdk.errors import UntapedError\n"
        "from untaped.sdk.process import run_process, start_process\n"
        "_process_groups.install()\n"
        "try:\n    signal.raise_signal(signal.SIGINT)\nexcept KeyboardInterrupt:\n    pass\n"
        "out = []\n"
        "def work():\n"
        f"    out.append(run_process(['touch', {str(tmp_path / 'ran')!r}], timeout=5).ended)\n"
        "    try:\n        start_process(['cat'])\n    except UntapedError as error:\n"
        "        out.append(error.category.value)\n"
        "worker = threading.Thread(target=work)\nworker.start()\nworker.join()\n"
        "print(out)\n"
        "print(run_process(['true'], timeout=5).ended)\n"  # the main thread still may
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["['stopped', 'interrupted']", "exited"]
    assert not (tmp_path / "ran").exists()


def test_without_the_installer_a_spawn_after_an_interrupt_still_starts() -> None:
    # Tests and library hosts install nothing: one interrupt must not stop every later spawn.
    _process_groups.ending(signal.SIGINT)
    results: list[ProcessResult] = []
    worker = threading.Thread(target=lambda: results.append(run_process(["true"], timeout=5)))
    worker.start()
    worker.join()
    assert results[0].ended == "exited"


@pytest.mark.parametrize("sig", ["SIGINT", "SIGTERM", "SIGHUP"])
def test_a_signal_while_the_main_thread_starts_a_child_still_stops_it(
    tmp_path: Path, sig: str
) -> None:
    # The signal lands after the fork, before Popen returns: it waits for the group, then acts.
    pid_file = tmp_path / "child.pid"
    code = (
        "import os, signal, subprocess, sys\nfrom untaped.sdk import _process_groups\n"
        "from untaped.sdk.process import run_process\n"
        f"_process_groups.GRACE_S = {GRACE}\n_process_groups.install()\n"
        "real = subprocess.Popen\n"
        "class Late(real):\n"
        "    def __init__(self, *args, **kwargs):\n"
        "        super().__init__(*args, **kwargs)\n"
        # Late enough that the child and its grandchild exist: both must be stopped.
        f"        while not os.path.exists({str(pid_file)!r} + '.grandchild'):\n"
        "            pass\n"
        f"        signal.raise_signal(signal.{sig})\n"
        "subprocess.Popen = Late\n"
        "try:\n"
        f"    run_process({_stubborn(pid_file, ignore='SIGINT, SIGTERM, SIGHUP')!r}, timeout=60)\n"
        "except KeyboardInterrupt:\n    print('interrupted', flush=True)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=30
    )
    assert "interrupted" in result.stdout, result.stderr
    assert _wait_gone(_pid(pid_file))
    assert _wait_gone(_pid(Path(f"{pid_file}.grandchild")))


def test_the_exit_net_stops_a_child_nobody_closed(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    code = (
        "from untaped.sdk import _process_groups\nfrom untaped.sdk.process import start_process\n"
        f"_process_groups.GRACE_S = {GRACE}\n"
        f"start_process({_stubborn(pid_file, ignore='SIGINT')!r})\n"
        f"import os, time\nwhile not os.path.exists({str(pid_file)!r} + '.grandchild'):\n"
        "    time.sleep(0.01)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    assert _wait_gone(_pid(pid_file))
    assert _wait_gone(_pid(Path(f"{pid_file}.grandchild")))


def test_a_forked_child_forgets_its_parents_groups() -> None:
    code = (
        "import os, sys\nfrom untaped.sdk import _process_groups\n"
        "from untaped.sdk.process import start_process\n"
        "child = start_process([sys.executable, '-c', 'import sys; sys.stdin.read()'])\n"
        "pid = os.fork()\n"
        "if pid == 0:\n    os._exit(0 if not _process_groups._S.groups else 1)\n"
        "print(os.waitpid(pid, 0)[1], bool(_process_groups._S.groups))\n"
        "child.close()\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=30
    )
    assert result.stdout.split() == ["0", "True"], result.stderr
