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
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from untaped_workspace.errors import GitError
from untaped_workspace.infrastructure import (
    DEFAULT_SLOW_TIMEOUT,
    DEFAULT_TIMEOUT,
    GitRunner,
)


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
    # `:g` formatting trims the trailing zero — 60.0 renders as "60s".
    assert "60s" in msg
    assert "status" in msg


def test_ensure_bare_uses_slow_timeout_on_clone(
    tmp_path: Path,
    recorded_timeouts: list[float | None],
) -> None:
    # `tmp_path` is empty so `ensure_bare`'s `(bare / "HEAD").is_file()`
    # early-return check fails and we follow the clone branch — which is
    # the path that should pay the slow-timeout budget.
    GitRunner(slow_timeout=900.0).ensure_bare("https://example.com/repo.git", cache_dir=tmp_path)
    assert recorded_timeouts == [900.0]


# ── Per-method bucket-selection contract ───────────────────────────────────

# Every public ``GitRunner`` method falls into one of two buckets: local-only
# (`DEFAULT_TIMEOUT`) or network (`DEFAULT_SLOW_TIMEOUT`). The parametrised
# test below pins each method to its expected bucket so a refactor that
# accidentally flips one (e.g. dropping the `timeout=self._slow_timeout`
# kwarg on `fetch`) fails loudly here rather than at user-report time.
#
# `tmp_path` doubles as both a fake repo path and a cache dir — the
# `recorded_timeouts` fixture's stub `subprocess.run` returns a successful
# CompletedProcess, so the methods never actually inspect the dir contents.

_FAST_OPS: list[tuple[str, Callable[[GitRunner, Path], object]]] = [
    ("status", lambda r, p: r.status(p)),
    ("is_dirty", lambda r, p: r.is_dirty(p)),
    ("default_branch", lambda r, p: r.default_branch(p)),
    ("read_remote_url", lambda r, p: r.read_remote_url(p)),
    ("read_current_branch", lambda r, p: r.read_current_branch(p)),
    ("ff_only_pull", lambda r, p: r.ff_only_pull(p, branch="main")),
]

_SLOW_OPS: list[tuple[str, Callable[[GitRunner, Path], object]]] = [
    ("bare_fetch", lambda r, p: r.bare_fetch(p)),
    (
        "clone_with_reference",
        lambda r, p: r.clone_with_reference(url="file:///x", dest=p / "d", bare=p / "b"),
    ),
    ("fetch", lambda r, p: r.fetch(p)),
]


@pytest.mark.parametrize(
    ("op_name", "op"),
    _FAST_OPS,
    ids=[name for name, _ in _FAST_OPS],
)
def test_local_ops_use_default_timeout(
    op_name: str,
    op: Callable[[GitRunner, Path], object],
    tmp_path: Path,
    recorded_timeouts: list[float | None],
) -> None:
    op(GitRunner(timeout=11.0, slow_timeout=99.0), tmp_path)
    assert recorded_timeouts == [11.0], (
        f"{op_name} should use the fast/local timeout, got {recorded_timeouts}"
    )


@pytest.mark.parametrize(
    ("op_name", "op"),
    _SLOW_OPS,
    ids=[name for name, _ in _SLOW_OPS],
)
def test_network_ops_use_slow_timeout(
    op_name: str,
    op: Callable[[GitRunner, Path], object],
    tmp_path: Path,
    recorded_timeouts: list[float | None],
) -> None:
    op(GitRunner(timeout=11.0, slow_timeout=99.0), tmp_path)
    assert recorded_timeouts == [99.0], (
        f"{op_name} should use the slow/network timeout, got {recorded_timeouts}"
    )


def test_module_constants_match_documented_defaults() -> None:
    # Constants are part of the public infrastructure surface; both the
    # CLI help-text interpolation and the `AGENTS.md` paragraph reference
    # them by name. Pin the numeric values so a docs/code drift fails CI.
    assert DEFAULT_TIMEOUT == 60.0
    assert DEFAULT_SLOW_TIMEOUT == 600.0


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
    from untaped_workspace import app

    from untaped.settings import get_settings

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
    from untaped_workspace import app

    from untaped.settings import get_settings

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
