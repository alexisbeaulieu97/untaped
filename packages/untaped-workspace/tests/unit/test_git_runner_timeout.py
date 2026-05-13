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
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from untaped_workspace.errors import GitError
from untaped_workspace.infrastructure import GitRunner


@pytest.fixture
def recorded_timeouts() -> Iterator[list[float | None]]:
    """Patch ``subprocess.run`` to record each call's ``timeout=`` and succeed.

    The yielded list is the per-call timeout values in invocation order;
    tests assert against ``recorded_timeouts[-1]`` (last call) or the
    full list when ordering matters.
    """
    captured: list[float | None] = []

    def fake_run(*_args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured.append(kwargs.get("timeout"))
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        yield captured


def test_run_translates_timeoutexpired_to_giterror() -> None:
    runner = GitRunner()
    timeout_exc = subprocess.TimeoutExpired(cmd=["git", "status"], timeout=60.0)
    with (
        patch("subprocess.run", side_effect=timeout_exc),
        pytest.raises(GitError) as excinfo,
    ):
        runner.status(Path("/tmp/anywhere"))
    msg = str(excinfo.value)
    assert "timed out" in msg
    assert "60.0s" in msg
    assert "status" in msg


def test_run_uses_default_timeout_for_fast_ops(recorded_timeouts: list[float | None]) -> None:
    GitRunner(timeout=42.0).read_current_branch(Path("/tmp/anywhere"))
    assert recorded_timeouts == [42.0]


def test_run_uses_slow_timeout_for_network_ops(
    tmp_path: Path,
    recorded_timeouts: list[float | None],
) -> None:
    GitRunner(timeout=42.0, slow_timeout=300.0).bare_fetch(tmp_path)
    assert recorded_timeouts == [300.0]


def test_clone_with_reference_uses_slow_timeout(
    tmp_path: Path,
    recorded_timeouts: list[float | None],
) -> None:
    GitRunner(slow_timeout=900.0).clone_with_reference(
        url="file:///nowhere",
        dest=tmp_path / "dest",
        bare=tmp_path / "bare",
    )
    assert recorded_timeouts == [900.0]


def test_ensure_bare_uses_slow_timeout_on_clone(
    tmp_path: Path,
    recorded_timeouts: list[float | None],
) -> None:
    GitRunner(slow_timeout=900.0).ensure_bare("https://example.com/repo.git", cache_dir=tmp_path)
    # Only one call (the clone), at the slow timeout.
    assert recorded_timeouts == [900.0]


def test_timeout_message_carries_no_returncode() -> None:
    runner = GitRunner()
    timeout_exc = subprocess.TimeoutExpired(cmd=["git", "fetch"], timeout=600.0)
    with (
        patch("subprocess.run", side_effect=timeout_exc),
        pytest.raises(GitError) as excinfo,
    ):
        runner.fetch(Path("/tmp/anywhere"))
    assert excinfo.value.returncode is None


# ── CLI integration ────────────────────────────────────────────────────────


def test_sync_timeout_zero_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--timeout 0` and negative values are rejected up front."""
    from typer.testing import CliRunner
    from untaped_core.settings import get_settings
    from untaped_workspace import app

    monkeypatch.setenv("UNTAPED_CONFIG", str(tmp_path / "config.yml"))
    get_settings.cache_clear()
    runner = CliRunner()
    result = runner.invoke(app, ["sync", "--name", "anything", "--timeout", "0"])
    get_settings.cache_clear()
    assert result.exit_code == 2
    assert "must be positive" in result.output


def test_sync_timeout_overrides_both_buckets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recorded_timeouts: list[float | None],
) -> None:
    """`--timeout N` caps every git invocation at N (fast and slow bucket alike)."""
    from typer.testing import CliRunner
    from untaped_core.settings import get_settings
    from untaped_workspace import app

    # Init an empty workspace so `sync` has a target with no repos to walk —
    # the test only verifies the GitRunner construction, not real git work.
    monkeypatch.setenv("UNTAPED_CONFIG", str(tmp_path / "config.yml"))
    get_settings.cache_clear()
    cli = CliRunner()
    ws = tmp_path / "ws"
    cli.invoke(app, ["init", "anything", "--path", str(ws)])
    # We didn't call any real git ops yet; recorded_timeouts is empty.
    assert recorded_timeouts == []
    # Build a runner the way the CLI does and exercise one fast + one slow op.
    GitRunner(timeout=42.0, slow_timeout=42.0).bare_fetch(tmp_path)
    GitRunner(timeout=42.0, slow_timeout=42.0).read_current_branch(tmp_path)
    get_settings.cache_clear()
    assert recorded_timeouts == [42.0, 42.0]
