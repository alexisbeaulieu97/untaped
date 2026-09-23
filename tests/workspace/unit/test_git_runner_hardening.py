"""Unit tests for ``GitRunner`` process hardening.

Pins the non-interactive subprocess contract (no credential prompts, no
repository discovery above the target directory) and cleanup of partial
clone destinations when git fails or times out.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from untaped.capabilities.workspace.errors import GitError
from untaped.capabilities.workspace.infrastructure import GitRunner


def _record_calls() -> tuple[list[dict[str, Any]], Any]:
    calls: list[dict[str, Any]] = []

    def fake_run(*_args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(kwargs)
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    return calls, fake_run


def test_git_runs_non_interactively(tmp_path: Path) -> None:
    calls, fake_run = _record_calls()
    repo = tmp_path / "ws" / "svc-a"
    with patch("subprocess.run", side_effect=fake_run):
        GitRunner().fetch(repo)
    (kwargs,) = calls
    assert kwargs["stdin"] is subprocess.DEVNULL
    env = kwargs["env"]
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GCM_INTERACTIVE"] == "never"


def test_ssh_runs_in_batch_mode_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
    monkeypatch.delenv("GIT_SSH", raising=False)
    calls, fake_run = _record_calls()
    with patch("subprocess.run", side_effect=fake_run):
        GitRunner().fetch(tmp_path / "ws" / "svc-a")
    assert calls[0]["env"]["GIT_SSH_COMMAND"] == "ssh -o BatchMode=yes"


@pytest.mark.parametrize(
    ("var", "value"), [("GIT_SSH_COMMAND", "ssh -i ~/.ssh/work"), ("GIT_SSH", "/usr/bin/plink")]
)
def test_user_ssh_override_is_respected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, var: str, value: str
) -> None:
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
    monkeypatch.delenv("GIT_SSH", raising=False)
    monkeypatch.setenv(var, value)
    calls, fake_run = _record_calls()
    with patch("subprocess.run", side_effect=fake_run):
        GitRunner().fetch(tmp_path / "ws" / "svc-a")
    env = calls[0]["env"]
    assert env[var] == value
    if var == "GIT_SSH":
        assert "GIT_SSH_COMMAND" not in env


def test_configured_core_ssh_command_is_not_overridden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # GIT_SSH_COMMAND outranks core.sshCommand, so the BatchMode default must
    # stay out of the way when the user configured ssh through git config.
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
    monkeypatch.delenv("GIT_SSH", raising=False)
    subprocess.run(
        ["git", "config", "--global", "core.sshCommand", "ssh -i ~/.ssh/work"],
        check=True,
    )
    calls, fake_run = _record_calls()
    with patch("subprocess.run", side_effect=fake_run):
        GitRunner().fetch(tmp_path / "ws" / "svc-a")
    assert "GIT_SSH_COMMAND" not in calls[0]["env"]


def test_per_repo_git_calls_set_ceiling_to_repo_parent(tmp_path: Path) -> None:
    calls, fake_run = _record_calls()
    repo = tmp_path / "ws" / "svc-a"
    with patch("subprocess.run", side_effect=fake_run):
        GitRunner().status(repo)
    ceilings = calls[0]["env"]["GIT_CEILING_DIRECTORIES"].split(os.pathsep)
    assert os.path.abspath(tmp_path / "ws") in ceilings


def _fake_git(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "fake-git"
    script.write_text(f"#!/bin/sh\nfor last; do :; done\n{body}\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell script stands in for git")
def test_clone_timeout_removes_partial_destination(tmp_path: Path) -> None:
    git = _fake_git(tmp_path, 'mkdir -p "$last/.git"; exec sleep 5')
    dest = tmp_path / "ws" / "svc-a"
    runner = GitRunner(git=str(git), slow_timeout=0.3)

    with pytest.raises(GitError, match="timed out"):
        runner.clone_with_reference(url="https://x/svc-a.git", dest=dest, bare=tmp_path / "b")

    assert not dest.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell script stands in for git")
def test_clone_failure_keeps_preexisting_destination(tmp_path: Path) -> None:
    git = _fake_git(tmp_path, 'echo "fatal: nope" >&2; exit 128')
    dest = tmp_path / "ws" / "svc-a"
    dest.mkdir(parents=True)
    (dest / "keep.txt").write_text("mine")

    with pytest.raises(GitError, match="fatal: nope"):
        GitRunner(git=str(git)).clone_with_reference(
            url="https://x/svc-a.git", dest=dest, bare=tmp_path / "b"
        )

    assert (dest / "keep.txt").read_text() == "mine"


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell script stands in for git")
def test_bare_clone_timeout_removes_partial_cache(tmp_path: Path) -> None:
    git = _fake_git(tmp_path, 'mkdir -p "$last"; touch "$last/HEAD"; exec sleep 5')
    runner = GitRunner(git=str(git), slow_timeout=0.3)
    cache = tmp_path / "cache"

    with pytest.raises(GitError, match="timed out"):
        runner.ensure_bare("https://x.example/org/svc-a.git", cache_dir=cache)

    assert not runner.bare_cache_path("https://x.example/org/svc-a.git", cache_dir=cache).exists()
