"""The store's per-repo lock: reentrant in a thread, busy while another process holds it."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from git_test_support import StoreError
from untaped.sdk import ErrorCategory
from untaped_git.repo_lock import repo_lock


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
