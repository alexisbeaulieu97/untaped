"""The `git.version` doctor row (S31's floor), and the store's reentrant repo lock."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from untaped import bootstrap
from untaped.sdk import ErrorCategory
from untaped.testing import CliInvoker, provider_candidate
from untaped.testing.git import git_shim
from untaped_git import SPEC
from untaped_git.errors import StoreError
from untaped_git.infrastructure.lock import repo_lock


def _version_row() -> tuple[int, dict[str, object]]:
    root = bootstrap.build_root_app(candidates=(provider_candidate(SPEC),))
    result = CliInvoker().invoke(root.meta, ["doctor", "--format", "json"])
    rows = {str(row["check"]): row for row in json.loads(result.stdout)}
    return result.exit_code, rows["git.version"]


def test_a_git_at_the_floor_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git_shim(tmp_path / "bin", monkeypatch, version="2.29.0")
    _, row = _version_row()
    assert (row["status"], row["detail"]) == ("pass", "git 2.29.0 (floor 2.29)")


def test_a_git_below_the_floor_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git_shim(tmp_path / "bin", monkeypatch, version="2.25.1")
    exit_code, row = _version_row()
    assert exit_code == 1
    assert row["status"] == "fail"
    assert "git 2.25.1 is older than 2.29" in str(row["detail"])


def test_no_git_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    _, row = _version_row()
    assert row["status"] == "warn"


def test_the_lock_is_reentrant(tmp_path: Path) -> None:
    repo = tmp_path / "store" / "app.git"
    with repo_lock(repo, timeout=1, error=StoreError), repo_lock(repo, timeout=1, error=StoreError):
        assert Path(f"{repo}.lock").exists()


def test_another_process_holding_the_lock_is_unavailable(tmp_path: Path) -> None:
    repo = tmp_path / "app.git"
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys, time; from filelock import FileLock\n"
            "with FileLock(sys.argv[1]):\n    print('held', flush=True); time.sleep(30)",
            f"{repo}.lock",
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "held"
        with (
            pytest.raises(StoreError, match="repo store is busy") as caught,
            repo_lock(repo, timeout=0.1, error=StoreError),
        ):
            pass
        assert caught.value.category == ErrorCategory.UNAVAILABLE
    finally:
        holder.kill()
        holder.wait()
