"""Unit tests for ``GitRunner``'s timeout translation.

The TimeoutExpired path is patched at the ``subprocess.run`` boundary
so the test never spawns a real subprocess (the integration tests in
``tests/integration/test_git_runner.py`` cover the happy path against
real git). These tests pin the contract:

- ``subprocess.TimeoutExpired`` becomes ``GitError`` with a message
  matching ``"git <args> timed out after <Ns>s"``.
- Network-op methods (``ensure_bare`` clone, ``bare_fetch``,
  ``clone_with_reference``, ``fetch``) use the ``slow_timeout``;
  everything else uses the default ``timeout``.
- Constructor overrides flow through ``_run``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from untaped_workspace.errors import GitError
from untaped_workspace.infrastructure import GitRunner


def _make_timeout(args: list[str], timeout: float) -> subprocess.TimeoutExpired:
    """Build a ``TimeoutExpired`` the way ``subprocess.run`` actually raises one."""
    return subprocess.TimeoutExpired(cmd=args, timeout=timeout)


def test_run_translates_timeoutexpired_to_giterror() -> None:
    runner = GitRunner()
    with (
        patch("subprocess.run", side_effect=_make_timeout(["git", "status"], 60.0)),
        pytest.raises(GitError) as excinfo,
    ):
        runner.status(Path("/tmp/anywhere"))
    msg = str(excinfo.value)
    assert "timed out" in msg
    assert "60.0s" in msg
    assert "status" in msg


def test_run_uses_default_timeout_for_fast_ops() -> None:
    runner = GitRunner(timeout=42.0)
    captured_timeout: dict[str, float | None] = {}

    def fake_run(*_args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured_timeout["value"] = kwargs.get("timeout")
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        runner.read_current_branch(Path("/tmp/anywhere"))
    assert captured_timeout["value"] == 42.0


def test_run_uses_slow_timeout_for_network_ops(tmp_path: Path) -> None:
    runner = GitRunner(timeout=42.0, slow_timeout=300.0)
    captured_timeout: dict[str, float | None] = {}

    def fake_run(*_args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured_timeout["value"] = kwargs.get("timeout")
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        runner.bare_fetch(tmp_path)
    assert captured_timeout["value"] == 300.0


def test_clone_with_reference_uses_slow_timeout(tmp_path: Path) -> None:
    runner = GitRunner(slow_timeout=900.0)
    captured_timeout: dict[str, float | None] = {}

    def fake_run(*_args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured_timeout["value"] = kwargs.get("timeout")
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        runner.clone_with_reference(
            url="file:///nowhere",
            dest=tmp_path / "dest",
            bare=tmp_path / "bare",
        )
    assert captured_timeout["value"] == 900.0


def test_ensure_bare_uses_slow_timeout_on_clone(tmp_path: Path) -> None:
    runner = GitRunner(slow_timeout=900.0)
    captured: list[float | None] = []

    def fake_run(*_args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured.append(kwargs.get("timeout"))
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        runner.ensure_bare("https://example.com/repo.git", cache_dir=tmp_path)
    # Only one call (the clone), and it's at the slow timeout.
    assert captured == [900.0]


def test_timeout_message_carries_no_returncode() -> None:
    runner = GitRunner()
    with (
        patch("subprocess.run", side_effect=_make_timeout(["git", "fetch"], 600.0)),
        pytest.raises(GitError) as excinfo,
    ):
        runner.fetch(Path("/tmp/anywhere"))
    assert excinfo.value.returncode is None
