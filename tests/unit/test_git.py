"""Tests for the shared hardened git subprocess module (``untaped.git``)."""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest

from untaped.errors import UntapedError
from untaped.git import (
    GitCommandError,
    git_auth_header,
    git_env,
    is_transient_failure,
    run_git,
    safe_cache_path,
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
    monkeypatch.setattr(subprocess, "run", _recording_run(calls))

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

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(GitCommandError, match="could not run"):
        run_git(["fetch"], timeout=5, auth_header=_HEADER)
    assert seen
    assert not seen[0].exists()


# ── run_git process contract ───────────────────────────────────────────────


def test_run_closes_stdin_discards_stdout_and_pipes_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(subprocess, "run", _recording_run(calls, stdout=b"chatter"))

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
    monkeypatch.setattr(subprocess, "run", _recording_run(calls, stdout=b"caf\xc3\xa9\n"))

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


def test_missing_git_binary_is_an_untaped_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _: None)
    with pytest.raises(UntapedError, match="`git` not found on PATH"):
        run_git(["status"], timeout=5)


def test_timeout_maps_to_timed_out_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def slow(args: list[str], **kwargs: Any) -> None:
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", slow)
    with pytest.raises(GitCommandError, match=r"git fetch timed out after 60s") as excinfo:
        run_git(["fetch", "--prune", "origin"], timeout=60.0)
    assert excinfo.value.timed_out is True
    assert excinfo.value.returncode is None


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell script stands in for git")
def test_timeout_kills_a_real_hung_process(tmp_path: Path) -> None:
    script = tmp_path / "fake-git"
    script.write_text("#!/bin/sh\nexec sleep 5\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    with pytest.raises(GitCommandError, match=r"timed out after 0\.2s"):
        run_git(["fetch"], timeout=0.2, git=str(script))


def test_failure_carries_status_and_stderr_gist(monkeypatch: pytest.MonkeyPatch) -> None:
    stderr = "warning: noise\nfatal: repository 'x' not found\n"
    monkeypatch.setattr(subprocess, "run", _recording_run([], returncode=128, stderr=stderr))

    with pytest.raises(GitCommandError) as excinfo:
        run_git(["clone", "--", "https://h/x.git", "/tmp/x"], timeout=5)

    assert str(excinfo.value) == "git clone failed: fatal: repository 'x' not found"
    assert excinfo.value.returncode == 128
    assert excinfo.value.stderr == stderr


def test_unchecked_failure_returns_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", _recording_run([], returncode=1, stderr=b"nope"))
    result = run_git(["grep", "x"], timeout=5, check=False)
    assert result.returncode == 1
    assert result.stderr == "nope"


def test_failure_redacts_auth_header_and_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    stderr = f"fatal: {_HEADER} rejected\nerror: token c2VjcmV0LXRva2Vu leaked\n"
    monkeypatch.setattr(subprocess, "run", _recording_run([], returncode=128, stderr=stderr))

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
    monkeypatch.setattr(subprocess, "run", _scripted_run([(128, _TRANSIENT), (0, "")], calls))

    result = run_git(["fetch"], timeout=5, retry_transient=True, sleep=sleeps.append)

    assert result.returncode == 0
    assert len(calls) == 2
    assert sleeps == [1.0]


def test_transient_retries_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    sleeps: list[float] = []
    monkeypatch.setattr(subprocess, "run", _scripted_run([(128, _TRANSIENT)], calls))

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
    monkeypatch.setattr(subprocess, "run", _scripted_run([(128, stderr)], calls))

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


# ── cache paths ────────────────────────────────────────────────────────────


def _legacy_cache_path_for(url: str, *, cache_dir: Path) -> Path:
    """Verbatim copy of the pre-consolidation github/ansible implementation."""

    def safe(value: str) -> str:
        return "".join(char if char.isalnum() or char in "._-" else "_" for char in value)

    parsed = urlparse(url)
    if parsed.scheme and parsed.path:
        base_name = Path(parsed.path.rstrip("/")).name
        host = parsed.netloc or "local"
    elif ":" in url and "@" in url.split(":", maxsplit=1)[0]:
        host_part, _, path_part = url.partition(":")
        host = host_part.rsplit("@", maxsplit=1)[-1]
        base_name = Path(path_part.rstrip("/")).name
    else:
        host = "local"
        base_name = Path(url.rstrip("/")).name
    if not base_name:
        base_name = "repository"
    if not base_name.endswith(".git"):
        base_name = f"{base_name}.git"
    digest = hashlib.sha256(url.encode()).hexdigest()[:16]
    return cache_dir.expanduser() / safe(host) / f"{safe(base_name[:-4])}-{digest}.git"


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/acme/api.git",
        "https://github.com/acme/api",
        "https://github.com/acme/api/",
        "https://ghe.example.com:8443/org/sub/repo.git",
        "https://user@github.com/acme/api.git",
        "http://github.com/acme/my repo.git",
        "git@github.com:acme/api.git",
        "git@github.com:acme/api",
        "ssh://git@github.com/acme/api.git",
        "file:///srv/git/repo.git",
        "file:///srv/git/",
        "/srv/git/local-repo",
        "relative/path",
        "https://github.com/",
        "https://github.com/acme/.git",
    ],
)
def test_safe_cache_path_matches_existing_cache_layout(url: str, tmp_path: Path) -> None:
    assert safe_cache_path(url, root=tmp_path) == _legacy_cache_path_for(url, cache_dir=tmp_path)


@pytest.mark.parametrize(
    "url",
    ["https://../x.git", "https://./..", "git@..:..", "../../..", "https://h/..%2F..%2Fetc.git"],
)
def test_safe_cache_path_is_confined_under_root(url: str, tmp_path: Path) -> None:
    root = tmp_path / "cache"
    path = safe_cache_path(url, root=root)
    assert path.resolve().is_relative_to(root.resolve())
    assert len(path.relative_to(root).parts) == 2
    assert path == safe_cache_path(url, root=root)


def test_safe_cache_path_expands_user(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    assert safe_cache_path("https://h/a.git", root=Path("~/c")).is_relative_to(tmp_path / "c")


@pytest.mark.parametrize(
    ("value", "expected"),
    [("acme/api", "acme_api"), ("..", "_"), (".", "_"), ("", "_"), ("v1.2-rc_3", "v1.2-rc_3")],
)
def test_safe_path_segment(value: str, expected: str) -> None:
    assert safe_path_segment(value) == expected
