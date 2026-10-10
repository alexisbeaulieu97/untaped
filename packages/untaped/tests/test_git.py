"""Tests for the shared hardened git subprocess module (``untaped.git``)."""

from __future__ import annotations

import os
import signal
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from untaped import git
from untaped.errors import UntapedError
from untaped.git import (
    GitCommandError,
    git_auth_header,
    git_env,
    git_toplevel,
    is_transient_failure,
    run_git,
    safe_path_segment,
    scoped_auth_config,
    stderr_gist,
)

_HEADER = "AUTHORIZATION: basic c2VjcmV0LXRva2Vu"


def _recording_run(
    calls: list[dict[str, Any]],
    *,
    returncode: int = 0,
    stdout: bytes | str = b"",
    stderr: bytes | str = b"",
) -> Any:
    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        config: str | None = None
        env = kwargs["env"]
        count = int(env.get("GIT_CONFIG_COUNT", "0"))
        if count:
            include = Path(env[f"GIT_CONFIG_VALUE_{count - 1}"])
            if include.name.startswith("untaped-git-auth-"):
                config = include.read_text()
                kwargs["auth_mode"] = stat.S_IMODE(include.stat().st_mode)
                kwargs["auth_path"] = include
        calls.append({"args": args, "auth_config": config, **kwargs})
        return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr=stderr)

    return fake_run


# ── environment hardening ──────────────────────────────────────────────────


def test_env_never_prompts_and_forces_c_locale(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LC_ALL", "fr_FR.UTF-8")
    monkeypatch.setenv("LANGUAGE", "fr")
    env = git_env()
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GCM_INTERACTIVE"] == "never"
    assert env["LC_ALL"] == "C"
    assert env["LANGUAGE"] == "C"


def test_env_can_keep_user_locale(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LC_ALL", "fr_FR.UTF-8")
    assert git_env(locale_c=False)["LC_ALL"] == "fr_FR.UTF-8"


@pytest.mark.parametrize(
    "name",
    ["GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_COMMON_DIR"],
)
def test_env_drops_inherited_repository_redirects(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setenv(name, "/elsewhere")
    assert name not in git_env()


def test_env_uses_ssh_batch_mode_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
    monkeypatch.delenv("GIT_SSH", raising=False)
    assert git_env(git_path="git")["GIT_SSH_COMMAND"] == "ssh -o BatchMode=yes"
    assert "GIT_SSH_COMMAND" not in git_env(batch_ssh=False)


@pytest.mark.parametrize(("var", "value"), [("GIT_SSH_COMMAND", "ssh -i k"), ("GIT_SSH", "plink")])
def test_env_respects_user_ssh_override(
    monkeypatch: pytest.MonkeyPatch, var: str, value: str
) -> None:
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
    monkeypatch.delenv("GIT_SSH", raising=False)
    monkeypatch.setenv(var, value)
    env = git_env(git_path="git")
    assert env[var] == value
    if var == "GIT_SSH":
        assert "GIT_SSH_COMMAND" not in env


def test_env_respects_configured_core_ssh_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
    monkeypatch.delenv("GIT_SSH", raising=False)
    subprocess.run(["git", "config", "--global", "core.sshCommand", "ssh -i k"], check=True)
    assert "GIT_SSH_COMMAND" not in git_env(git_path="git")


def test_core_ssh_command_is_probed_in_the_target_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
    monkeypatch.delenv("GIT_SSH", raising=False)
    configured, plain = tmp_path / "configured", tmp_path / "plain"
    for repo in (configured, plain):
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(configured), "config", "core.sshCommand", "ssh -i k"], check=True
    )
    monkeypatch.chdir(plain)
    assert "GIT_SSH_COMMAND" not in git_env(git_path="git", cwd=configured)
    monkeypatch.chdir(configured)
    assert git_env(git_path="git", cwd=plain)["GIT_SSH_COMMAND"] == "ssh -o BatchMode=yes"


def test_core_ssh_command_is_read_through_a_bare_git_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under ``safe.bareRepository=explicit`` the probe names the bare repo, not just its cwd."""
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
    monkeypatch.delenv("GIT_SSH", raising=False)
    bare = tmp_path / "cache.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    command = ["git", "--git-dir", str(bare), "config", "core.sshCommand", "ssh -i k"]
    subprocess.run(command, check=True)
    assert "GIT_SSH_COMMAND" not in git_env(git_path="git", cwd=bare, git_dir=bare)


def test_ceiling_stops_discovery_above_cwd(tmp_path: Path) -> None:
    env = git_env(cwd=tmp_path / "ws" / "repo", ceiling=True)
    assert os.path.abspath(tmp_path / "ws") in env["GIT_CEILING_DIRECTORIES"].split(os.pathsep)
    assert "GIT_CEILING_DIRECTORIES" not in git_env(cwd=tmp_path)


def test_auth_env_includes_config_and_scrubs_traces(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in ("GIT_TRACE", "GIT_TRACE_CURL", "GIT_TRACE2_EVENT", "GIT_CURL_VERBOSE"):
        monkeypatch.setenv(name, "1")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "grep.patternType")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "fixed")
    include = tmp_path / "auth.config"

    env = git_env(auth_config=include)

    assert not any(key.startswith("GIT_TRACE") for key in env)
    assert "GIT_CURL_VERBOSE" not in env
    assert env["GIT_CONFIG_KEY_0"] == "grep.patternType"
    assert env["GIT_CONFIG_KEY_1"] == "include.path"
    assert env["GIT_CONFIG_VALUE_1"] == str(include)
    assert env["GIT_CONFIG_COUNT"] == "2"
    assert "GIT_TRACE" in git_env()


# ── auth include file ──────────────────────────────────────────────────────


def test_git_auth_header_encodes_token() -> None:
    assert git_auth_header("tok") == "AUTHORIZATION: basic eC1hY2Nlc3MtdG9rZW46dG9r"


def test_scoped_auth_config_is_private_scoped_and_removed() -> None:
    with scoped_auth_config(_HEADER, auth_url="https://github.example.com/acme/api.git") as path:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert path.read_text() == (
            f'[http "https://github.example.com/"]\n\textraheader = {_HEADER}\n'
        )
    assert not path.exists()


def test_unscoped_auth_config_applies_to_all_http_remotes() -> None:
    with scoped_auth_config(_HEADER) as path:
        assert path.read_text().startswith("[http]\n")


def test_scoped_auth_config_rejects_non_https_url() -> None:
    with (
        pytest.raises(ValueError, match="https"),
        scoped_auth_config(_HEADER, auth_url="git@github.com:acme/api.git"),
    ):
        pass


def test_auth_header_travels_only_through_removed_include_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(git, "_run_process", _recording_run(calls))

    run_git(["fetch", "origin"], timeout=5, auth_header=_HEADER, auth_url="https://h/a.git")

    (call,) = calls
    assert _HEADER not in " ".join(call["args"])
    assert not any(_HEADER in value for value in call["env"].values())
    assert call["auth_config"] == f'[http "https://h/"]\n\textraheader = {_HEADER}\n'
    assert call["auth_mode"] == 0o600
    assert not call["auth_path"].exists()


def test_auth_include_is_removed_when_git_cannot_start(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Path] = []

    def boom(args: list[str], **kwargs: Any) -> None:
        env = kwargs["env"]
        seen.append(Path(env[f"GIT_CONFIG_VALUE_{int(env['GIT_CONFIG_COUNT']) - 1}"]))
        raise OSError("exec format error")

    monkeypatch.setattr(git, "_run_process", boom)
    with pytest.raises(GitCommandError, match="could not run"):
        run_git(["fetch"], timeout=5, auth_header=_HEADER)
    assert seen
    assert not seen[0].exists()


# ── run_git process contract ───────────────────────────────────────────────


def test_run_closes_stdin_discards_stdout_and_pipes_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(git, "_run_process", _recording_run(calls, stdout=b"chatter"))

    result = run_git(["status"], timeout=7)

    (call,) = calls
    assert call["stdin"] is subprocess.DEVNULL
    assert call["input"] is None
    assert call["stdout"] is subprocess.DEVNULL
    assert call["stderr"] is subprocess.PIPE
    assert call["timeout"] == 7
    assert result.stdout == b""


def test_run_feeds_stdin_and_captures_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(git, "_run_process", _recording_run(calls, stdout=b"caf\xc3\xa9\n"))

    result = run_git(["cat-file", "--batch"], timeout=5, capture=True, stdin="abc\n")

    assert calls[0]["stdin"] is None
    assert calls[0]["input"] == b"abc\n"
    assert calls[0]["stdout"] is subprocess.PIPE
    assert result.stdout == b"caf\xc3\xa9\n"
    assert result.text == "café\n"


def test_run_really_executes_git(tmp_path: Path) -> None:
    run_git(["init", "-q", str(tmp_path / "r")], timeout=30)
    result = run_git(
        ["rev-parse", "--is-bare-repository"], cwd=tmp_path / "r", timeout=30, capture=True
    )
    assert result.text.strip() == "false"


def test_git_toplevel_finds_the_checkout_root_from_a_subdirectory(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    run_git(["init", "-q", str(repo)], timeout=30)
    nested = repo / "a" / "b"
    nested.mkdir(parents=True)

    assert git_toplevel(nested) == repo.resolve()
    assert git_toplevel(repo) == repo.resolve()


def test_git_toplevel_is_local_so_it_skips_the_ssh_setup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
    monkeypatch.setattr(git, "_run_process", _recording_run(calls, stdout=f"{tmp_path}\n"))
    monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_k: pytest.fail("probed ssh config"))

    assert git_toplevel(tmp_path) == tmp_path.resolve()
    assert "GIT_SSH_COMMAND" not in calls[0]["env"]


def test_git_toplevel_is_none_outside_a_checkout(tmp_path: Path) -> None:
    assert git_toplevel(tmp_path) is None


def test_git_toplevel_surfaces_a_missing_git(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("shutil.which", lambda _: None)
    with pytest.raises(GitCommandError, match="`git` not found on PATH"):
        git_toplevel(tmp_path)


def test_missing_git_binary_is_an_untaped_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _: None)
    with pytest.raises(UntapedError, match="`git` not found on PATH"):
        run_git(["status"], timeout=5)


def test_timeout_maps_to_timed_out_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def slow(args: list[str], **kwargs: Any) -> None:
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr(git, "_run_process", slow)
    with pytest.raises(GitCommandError, match=r"git fetch timed out after 60s") as excinfo:
        run_git(["fetch", "--prune", "origin"], timeout=60.0)
    assert excinfo.value.timed_out is True
    assert excinfo.value.returncode is None


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable script stands in for git")
def test_timeout_kills_a_real_hung_process(tmp_path: Path) -> None:
    pid_file = tmp_path / "git.pid"
    staged = tmp_path / "git.pid.tmp"
    script = tmp_path / "fake-git"
    script.write_text(
        f"#!{sys.executable}\n"
        "import os, sys, time\n"
        # The SSH config probe must complete before the timed command starts.
        "if sys.argv[1] == 'config':\n    sys.exit(1)\n"
        # Staged then renamed: the timeout may kill it mid-write, never leaving a partial pid.
        f"with open({str(staged)!r}, 'w') as f:\n    f.write(str(os.getpid()))\n"
        f"os.replace({str(staged)!r}, {str(pid_file)!r})\n"
        "time.sleep(60)\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    with pytest.raises(GitCommandError, match=r"timed out after 0\.2s"):
        run_git(["fetch"], timeout=0.2, git=str(script))
    if pid_file.exists():  # else it was killed before it got that far
        with pytest.raises(ProcessLookupError):
            os.kill(int(pid_file.read_text()), 0)


def _gone(pid: int) -> bool:
    """Whether ``pid`` has exited (a zombie no reaper collected counts as gone)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    stat_file = Path(f"/proc/{pid}/stat")
    try:
        return stat_file.read_text().rsplit(")", 1)[1].split()[0] == "Z"
    except OSError:
        return False


def _wait_gone(pid: int, seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while not _gone(pid):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.02)
    return True


def _fake_git_with_helper(
    tmp_path: Path, *, helper_ignores_term: bool = True
) -> tuple[Path, Path, Path]:
    """A fake git that starts a hung helper, like ``remote-http`` or ``fetch-pack``.

    On SIGTERM it removes its lock file, as git does; the helper ignores
    SIGTERM unless told otherwise, so only SIGKILL (or SIGINT) stops it.
    It writes ``ready`` once the lock exists and the helper has started up
    (a Python helper interrupted mid-startup can swallow its KeyboardInterrupt).
    """
    ignore = "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n" if helper_ignores_term else ""
    helper_pid = tmp_path / "helper.pid"
    lock = tmp_path / "shallow.lock"
    helper = tmp_path / "helper.py"
    helper.write_text(
        f"import os, signal, time\n{ignore}"
        f"with open({str(helper_pid)!r} + '.tmp', 'w') as f:\n    f.write(str(os.getpid()))\n"
        f"os.replace({str(helper_pid)!r} + '.tmp', {str(helper_pid)!r})\n"
        "time.sleep(60)\n"
    )
    script = tmp_path / "fake-git"
    script.write_text(
        f"#!{sys.executable}\n"
        "import os, signal, subprocess, sys, time\n"
        "if sys.argv[1] == 'config':\n    sys.exit(1)\n"
        f"lock = {str(lock)!r}\n"
        "def cleanup(*_):\n    os.unlink(lock)\n    sys.exit(143)\n"
        "signal.signal(signal.SIGTERM, cleanup)\n"
        f"subprocess.Popen([sys.executable, {str(helper)!r}])\n"
        f"while not os.path.exists({str(helper_pid)!r}):\n    time.sleep(0.01)\n"
        "open(lock, 'w').close()\n"
        f"open({str(tmp_path / 'ready')!r}, 'w').close()\n"
        "time.sleep(60)\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script, helper_pid, lock


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
def test_timeout_kills_the_helpers_git_started(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(git, "_TERM_GRACE_S", 0.5)
    script, helper_pid, _ = _fake_git_with_helper(tmp_path)

    with pytest.raises(GitCommandError, match="timed out"):
        run_git(["fetch"], timeout=1.5, git=str(script))

    assert helper_pid.exists(), "the fake git did not get far enough to start its helper"
    assert _wait_gone(int(helper_pid.read_text())), "git's helper outlived the timeout"


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
def test_timeout_lets_git_clean_up_its_locks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(git, "_TERM_GRACE_S", 0.5)
    script, _, lock = _fake_git_with_helper(tmp_path)

    with pytest.raises(GitCommandError, match="timed out"):
        run_git(["fetch"], timeout=1.5, git=str(script))

    assert (tmp_path / "ready").exists(), "the fake git did not get far enough to take its lock"
    assert not lock.exists(), "git was killed before it could remove its lock"


@pytest.mark.skipif(
    os.name == "nt" or threading.current_thread() is not threading.main_thread(),
    reason="POSIX process groups; signals reach only the main thread",
)
def test_an_interrupt_stops_git_and_its_helpers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Git runs in its own session, so a terminal Ctrl-C no longer reaches it:
    # run_git must stop the group itself.
    script, helper_pid, lock = _fake_git_with_helper(tmp_path)

    def interrupt(*_: object) -> None:
        if lock.exists():
            raise KeyboardInterrupt
        signal.setitimer(signal.ITIMER_REAL, 0.1)

    monkeypatch.setattr(git, "_TERM_GRACE_S", 0.5)
    previous = signal.signal(signal.SIGALRM, interrupt)
    try:
        signal.setitimer(signal.ITIMER_REAL, 0.1)
        with pytest.raises(KeyboardInterrupt):
            run_git(["fetch"], timeout=30, git=str(script))
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)

    assert _wait_gone(int(helper_pid.read_text()))
    assert not lock.exists()


def _wait_for(path: Path, seconds: float = 10.0) -> None:
    deadline = time.monotonic() + seconds
    while not path.exists():
        assert time.monotonic() < deadline, f"{path.name} never appeared"
        time.sleep(0.02)


@pytest.fixture
def restore_signal_handlers() -> Iterator[None]:
    saved = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    # A background or nohup run inherits SIG_IGN, which forward_signals leaves alone.
    signal.signal(signal.SIGINT, signal.default_int_handler)
    yield
    for sig, handler in saved.items():
        signal.signal(sig, handler)


@pytest.mark.skipif(
    os.name == "nt" or threading.current_thread() is not threading.main_thread(),
    reason="POSIX process groups; signals reach only the main thread",
)
@pytest.mark.usefixtures("restore_signal_handlers")
def test_ctrl_c_reaches_git_started_by_a_worker_thread(tmp_path: Path) -> None:
    # Only the main thread sees KeyboardInterrupt: git in a worker would run
    # on until its timeout unless the signal is passed on to its group.
    script, helper_pid, _ = _fake_git_with_helper(tmp_path)
    git.forward_signals()
    outcome: list[BaseException] = []

    def fetch() -> None:
        try:
            run_git(["fetch"], timeout=30, git=str(script))
        except GitCommandError as exc:
            outcome.append(exc)

    worker = threading.Thread(target=fetch)
    worker.start()
    _wait_for(tmp_path / "ready")
    with pytest.raises(KeyboardInterrupt):
        os.kill(os.getpid(), signal.SIGINT)
        time.sleep(5)
    # Well under the 30s git timeout: only the forwarded signal ends it in time.
    worker.join(timeout=15)

    assert not worker.is_alive(), "git in the worker kept running after Ctrl-C"
    assert outcome, "git exited cleanly instead of being interrupted"
    assert _wait_gone(int(helper_pid.read_text()))


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
@pytest.mark.parametrize("sig", ["SIGTERM", "SIGHUP"])
def test_a_signal_that_ends_untaped_reaches_git(tmp_path: Path, sig: str) -> None:
    script, helper_pid, _ = _fake_git_with_helper(tmp_path, helper_ignores_term=False)
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal\n"
            "from untaped.git import forward_signals, run_git\n"
            f"signal.signal(signal.{sig}, signal.SIG_DFL)\n"
            "forward_signals()\n"
            f"run_git(['fetch'], timeout=30, git={str(script)!r})\n",
        ],
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for(tmp_path / "ready")
        child.send_signal(getattr(signal, sig))
        assert child.wait(timeout=10) == -getattr(signal, sig)
    finally:
        child.kill()
        child.wait()

    assert _wait_gone(int(helper_pid.read_text())), f"git's helper outlived {sig} to untaped"


@pytest.mark.usefixtures("restore_signal_handlers")
def test_forwarding_leaves_an_ignored_signal_ignored() -> None:
    # nohup: SIGHUP stays ignored, for untaped and the git it starts.
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    git.forward_signals()
    assert signal.getsignal(signal.SIGHUP) == signal.SIG_IGN
    assert signal.getsignal(signal.SIGTERM) != signal.SIG_DFL


def test_a_forwarded_default_signal_still_ends_untaped(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, int]] = []
    monkeypatch.setattr(git.signal, "signal", lambda sig, handler: calls.append(("reset", sig)))
    monkeypatch.setattr(git.os, "kill", lambda pid, sig: calls.append(("kill", sig)))

    def exit_(status: int) -> None:
        calls.append(("exit", status))
        raise SystemExit(status)

    monkeypatch.setattr(git.os, "_exit", exit_)

    with pytest.raises(SystemExit):
        git._forward_signal(signal.SIG_DFL, signal.SIGTERM, None)

    # Re-raised under the default action; exiting covers PID 1, which ignores it.
    assert calls == [
        ("reset", signal.SIGTERM),
        ("kill", signal.SIGTERM),
        ("exit", 128 + signal.SIGTERM),
    ]


@pytest.mark.usefixtures("restore_signal_handlers")
def test_without_process_groups_git_is_killed_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(os, "killpg")
    killed: list[bool] = []

    class _Process:
        pid = 0

        def kill(self) -> None:
            killed.append(True)

    git._stop_group(_Process())  # type: ignore[arg-type]
    before = signal.getsignal(signal.SIGTERM)
    git.forward_signals()

    assert killed == [True]
    assert signal.getsignal(signal.SIGTERM) is before


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
def test_a_signal_forwarded_while_git_starts_still_reaches_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    script, _, _ = _fake_git_with_helper(tmp_path, helper_ignores_term=False)
    monkeypatch.setattr(git, "_FORWARDED", [])
    real_popen = subprocess.Popen

    def popen(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        process = real_popen(*args, **kwargs)
        if kwargs.get("start_new_session"):
            _wait_for(tmp_path / "ready")
            git._forward_signal(lambda *_: None, signal.SIGTERM, None)  # before registration
        return process

    monkeypatch.setattr(git.subprocess, "Popen", popen)
    started = time.monotonic()
    with pytest.raises(GitCommandError, match="failed"):
        run_git(["fetch"], timeout=30, git=str(script))
    assert time.monotonic() - started < 15, "git ran on until its timeout"


def _die_of_default_signal(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, int]]:
    """Make _forward_signal's SIG_DFL branch record signals and raise SystemExit."""
    signalled: list[tuple[int, int]] = []
    monkeypatch.setattr(git, "_signal_group", lambda pgid, sig: signalled.append((pgid, sig)))
    monkeypatch.setattr(git, "_FORWARDED", [])
    monkeypatch.setattr(git.signal, "signal", lambda *_: None)
    monkeypatch.setattr(git.os, "kill", lambda *_: None)

    def exit_(status: int) -> None:
        raise SystemExit(status)

    monkeypatch.setattr(git.os, "_exit", exit_)
    return signalled


def test_a_signal_that_ends_untaped_waits_for_a_git_being_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signalled = _die_of_default_signal(monkeypatch)
    live: dict[int, None] = {}
    spawning: dict[int, None] = {-1: None}  # another thread is forking git
    monkeypatch.setattr(git, "_LIVE_GROUPS", live)
    monkeypatch.setattr(git, "_SPAWNING", spawning)

    def register() -> None:
        time.sleep(0.1)
        live[4242] = None
        spawning.clear()

    registrar = threading.Thread(target=register)
    registrar.start()
    with pytest.raises(SystemExit):
        git._forward_signal(signal.SIG_DFL, signal.SIGTERM, None)
    registrar.join()

    assert (4242, signal.SIGTERM) in signalled


def test_a_signal_that_ends_untaped_does_not_wait_for_its_own_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _die_of_default_signal(monkeypatch)
    # The handler interrupted this thread mid-spawn: that spawn cannot finish.
    monkeypatch.setattr(git, "_SPAWNING", {threading.get_ident(): None})
    monkeypatch.setattr(git, "_SPAWN_WAIT_S", 30.0)

    started = time.monotonic()
    with pytest.raises(SystemExit):
        git._forward_signal(signal.SIG_DFL, signal.SIGTERM, None)
    assert time.monotonic() - started < 5


def test_no_git_is_left_marked_as_starting(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    spawning: dict[int, None] = {}
    monkeypatch.setattr(git, "_SPAWNING", spawning)
    real_popen = subprocess.Popen

    def popen(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        if kwargs.get("start_new_session"):
            assert spawning, "git forked without being marked as starting"
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(git.subprocess, "Popen", popen)
    run_git(["init", "-q", str(tmp_path / "r")], timeout=30, batch_ssh=False)
    assert spawning == {}

    def no_exec(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        if kwargs.get("start_new_session"):
            raise OSError("exec format error")
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(git.subprocess, "Popen", no_exec)
    with pytest.raises(GitCommandError, match="could not run"):
        run_git(["status"], timeout=5, batch_ssh=False)
    assert spawning == {}


def test_git_is_registered_before_its_spawn_mark_clears(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Cleared before registering, a dying untaped would see neither the spawn
    # nor the group; cleared only at the end, it would wait on every running git.
    spawning: dict[int, None] = {}
    me = threading.get_ident()

    class _Live(dict[int, None]):
        def __setitem__(self, key: int, value: None) -> None:
            assert me in spawning, "spawn mark cleared before git was registered"
            super().__setitem__(key, value)

    class _Popen(subprocess.Popen[bytes]):
        def communicate(self, *args: Any, **kwargs: Any) -> Any:
            assert me not in spawning, "spawn mark still set while git runs"
            return super().communicate(*args, **kwargs)

    monkeypatch.setattr(git, "_SPAWNING", spawning)
    monkeypatch.setattr(git, "_LIVE_GROUPS", _Live())
    monkeypatch.setattr(git.subprocess, "Popen", _Popen)

    run_git(["init", "-q", str(tmp_path / "r")], timeout=30, batch_ssh=False)
    assert spawning == {}


def test_failure_carries_status_and_stderr_gist(monkeypatch: pytest.MonkeyPatch) -> None:
    stderr = "warning: noise\nfatal: repository 'x' not found\n"
    monkeypatch.setattr(git, "_run_process", _recording_run([], returncode=128, stderr=stderr))

    with pytest.raises(GitCommandError) as excinfo:
        run_git(["clone", "--", "https://h/x.git", "/tmp/x"], timeout=5)

    assert str(excinfo.value) == "git clone failed: fatal: repository 'x' not found"
    assert excinfo.value.returncode == 128
    assert excinfo.value.stderr == stderr


def test_unchecked_failure_returns_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(git, "_run_process", _recording_run([], returncode=1, stderr=b"nope"))
    result = run_git(["grep", "x"], timeout=5, check=False)
    assert result.returncode == 1
    assert result.stderr == "nope"


def test_failure_redacts_auth_header_and_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    stderr = f"fatal: {_HEADER} rejected\nerror: token c2VjcmV0LXRva2Vu leaked\n"
    monkeypatch.setattr(git, "_run_process", _recording_run([], returncode=128, stderr=stderr))

    with pytest.raises(GitCommandError) as excinfo:
        run_git(["fetch"], timeout=5, auth_header=_HEADER)

    assert "c2VjcmV0LXRva2Vu" not in str(excinfo.value)
    assert "c2VjcmV0LXRva2Vu" not in excinfo.value.stderr
    assert "fatal: <redacted> rejected" in str(excinfo.value)


def test_stderr_gist_prefers_fatal_lines_and_is_bounded() -> None:
    assert stderr_gist("") == "no stderr"
    assert stderr_gist("hint: a\nlast line\n") == "last line"
    assert stderr_gist("error: one\nnoise\nfatal: two") == "error: one; fatal: two"
    long = stderr_gist("fatal: " + "x" * 1000)
    assert len(long) == 300
    assert long.endswith("...")


# ── transient retry ────────────────────────────────────────────────────────

_TRANSIENT = "error: RPC failed; curl 56 GnuTLS recv error (-110)\nfatal: early EOF\n"


def _scripted_run(outcomes: list[tuple[int, str]], calls: list[list[str]]) -> Any:
    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(args)
        code, stderr = outcomes[min(len(calls), len(outcomes)) - 1]
        return subprocess.CompletedProcess(args, code, stdout=b"", stderr=stderr.encode())

    return fake_run


def test_transient_failure_is_retried_with_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    sleeps: list[float] = []
    monkeypatch.setattr(git, "_run_process", _scripted_run([(128, _TRANSIENT), (0, "")], calls))

    result = run_git(["fetch"], timeout=5, retry_transient=True, sleep=sleeps.append)

    assert result.returncode == 0
    assert len(calls) == 2
    assert sleeps == [1.0]


def test_transient_retries_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    sleeps: list[float] = []
    monkeypatch.setattr(git, "_run_process", _scripted_run([(128, _TRANSIENT)], calls))

    with pytest.raises(GitCommandError, match="early EOF") as excinfo:
        run_git(["fetch"], timeout=5, retry_transient=True, attempts=3, sleep=sleeps.append)

    assert len(calls) == 3
    assert sleeps == [1.0, 2.0]
    assert "git fetch failed after 3 attempts" in str(excinfo.value)


@pytest.mark.parametrize(
    ("stderr", "retry_transient"),
    [("fatal: repository 'x' not found", True), (_TRANSIENT, False)],
    ids=["permanent", "retry-not-requested"],
)
def test_failure_is_not_retried(
    monkeypatch: pytest.MonkeyPatch, stderr: str, retry_transient: bool
) -> None:
    calls: list[list[str]] = []
    sleeps: list[float] = []
    monkeypatch.setattr(git, "_run_process", _scripted_run([(128, stderr)], calls))

    with pytest.raises(GitCommandError):
        run_git(["fetch"], timeout=5, retry_transient=retry_transient, sleep=sleeps.append)

    assert len(calls) == 1
    assert sleeps == []


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        ("fatal: unable to access 'x': Could not resolve host: github.com", True),
        ("error: RPC failed; HTTP 503 curl 22 The requested URL returned error: 503", True),
        ("fatal: the remote end hung up unexpectedly", True),
        ("fatal: Authentication failed for 'https://x/'", False),
        ("fatal: couldn't find remote ref refs/heads/missing", False),
    ],
)
def test_transient_classifier(stderr: str, expected: bool) -> None:
    assert is_transient_failure(stderr) is expected


@pytest.mark.parametrize(
    ("stderr", "category"),
    [
        ("fatal: Authentication failed for 'https://x/'", "auth"),
        (
            "fatal: could not read Username for 'https://github.com': terminal prompts disabled",
            "auth",
        ),
        (
            "git@github.com: Permission denied (publickey).\nfatal: Could not read from remote",
            "auth",
        ),
        # credential.interactive=never (git >= 2.46)
        ("fatal: unable to get password from user", "auth"),
        ("fatal: unable to access 'x': The requested URL returned error: 401", "auth"),
        ("fatal: unable to access 'x': The requested URL returned error: 403", "permission"),
        ("fatal: couldn't find remote ref refs/heads/missing", "failed"),
    ],
)
def test_a_rejected_credential_is_an_environment_failure(
    monkeypatch: pytest.MonkeyPatch, stderr: str, category: str
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(git, "_run_process", _scripted_run([(128, stderr)], calls))

    with pytest.raises(GitCommandError) as excinfo:
        run_git(["fetch"], timeout=5, retry_transient=True, sleep=lambda _: None)

    assert (excinfo.value.category, excinfo.value.system) == (category, "git")
    assert len(calls) == 1
    if category != "failed":
        assert excinfo.value.hint


@pytest.mark.parametrize(
    ("stderr", "fix"),
    [
        # A host it would have asked to trust.
        (
            "Host key verification failed.\nfatal: Could not read from remote repository.",
            "git fetch",
        ),
        # The user allowed ssh's graphical prompt, but there is no program.
        ("ssh_askpass: exec(/usr/bin/ssh-askpass): No such file or directory\n", "ssh-agent"),
        # ssh says nothing about the passphrase it could not ask for.
        (
            "git@github.com: Permission denied (publickey).\nfatal: Could not read from remote",
            "ssh-agent",
        ),
    ],
)
def test_an_ssh_failure_says_git_has_no_terminal(
    monkeypatch: pytest.MonkeyPatch, stderr: str, fix: str
) -> None:
    monkeypatch.setattr(git, "_run_process", _scripted_run([(128, stderr)], []))

    with pytest.raises(GitCommandError) as excinfo:
        run_git(["fetch"], timeout=5)

    assert excinfo.value.hint is not None
    assert "without a terminal" in excinfo.value.hint
    assert fix in excinfo.value.hint


@pytest.mark.parametrize(
    "banner",
    ["WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!", "WARNING: REVOKED HOST KEY DETECTED!"],
)
def test_an_untrusted_host_key_gets_no_hint_to_accept_it(
    monkeypatch: pytest.MonkeyPatch, banner: str
) -> None:
    stderr = f"@@@ {banner} @@@\nHost key verification failed.\nfatal: Could not read"
    monkeypatch.setattr(git, "_run_process", _scripted_run([(128, stderr)], []))

    with pytest.raises(GitCommandError) as excinfo:
        run_git(["fetch"], timeout=5)

    assert excinfo.value.hint is None


def test_ssh_and_git_never_fall_back_to_a_graphical_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SSH_ASKPASS_REQUIRE", raising=False)
    monkeypatch.delenv("GIT_ASKPASS", raising=False)
    env = git_env(batch_ssh=False)
    assert env["SSH_ASKPASS_REQUIRE"] == "never"
    assert env["GIT_ASKPASS"] == ""  # git then skips core.askPass and SSH_ASKPASS

    monkeypatch.setenv("SSH_ASKPASS_REQUIRE", "")  # ssh reads empty as unset
    assert git_env(batch_ssh=False)["SSH_ASKPASS_REQUIRE"] == "never"

    monkeypatch.setenv("SSH_ASKPASS_REQUIRE", "force")
    monkeypatch.setenv("GIT_ASKPASS", "/ci/token-helper")
    env = git_env(batch_ssh=False)
    assert (env["SSH_ASKPASS_REQUIRE"], env["GIT_ASKPASS"]) == ("force", "/ci/token-helper")


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell script stands in for askpass")
def test_git_does_not_run_an_inherited_ssh_askpass_for_https_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log = tmp_path / "askpass.log"
    askpass = tmp_path / "askpass"
    askpass.write_text(f"#!/bin/sh\necho called >> {log}\necho x\n")
    askpass.chmod(askpass.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("SSH_ASKPASS", str(askpass))
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("GIT_ASKPASS", raising=False)
    server = _unauthorized_http_server()
    try:
        with pytest.raises(GitCommandError, match="terminal prompts disabled"):
            run_git(
                [
                    "-c",
                    "credential.helper=",
                    "-c",
                    "http.proxy=",  # reach the server even behind a configured proxy
                    "-c",
                    "credential.interactive=true",  # git >= 2.46: undo conftest's "never"
                    "ls-remote",
                    f"http://127.0.0.1:{server.server_port}/r",
                ],
                timeout=30,
                batch_ssh=False,
            )
    finally:
        server.shutdown()
        server.server_close()

    assert not log.exists(), "git ran the inherited SSH_ASKPASS"


def _unauthorized_http_server() -> Any:
    import http.server

    class _Unauthorized(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="r"')
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Unauthorized)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# ── path segments ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [("acme/api", "acme_api"), ("..", "_"), (".", "_"), ("", "_"), ("v1.2-rc_3", "v1.2-rc_3")],
)
def test_safe_path_segment(value: str, expected: str) -> None:
    assert safe_path_segment(value) == expected
