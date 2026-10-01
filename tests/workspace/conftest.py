"""Real-git fixtures: throwaway upstream repos and an isolated workspace config."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from untaped.settings import get_settings


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.fixture
def make_upstream(tmp_path: Path) -> Callable[..., Path]:
    """Create a bare upstream repo with one commit on ``main``; return its path."""
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")

    def make(name: str = "api", *, branches: tuple[str, ...] = ()) -> Path:
        bare = tmp_path / "remotes" / "acme" / f"{name}.git"
        bare.parent.mkdir(parents=True, exist_ok=True)
        git(tmp_path, "init", "-q", "--bare", "--initial-branch=main", str(bare))
        seed = tmp_path / f"_seed_{name}"
        git(tmp_path, "clone", "-q", str(bare), str(seed))
        for key, value in (("user.email", "t@t"), ("user.name", "t"), ("commit.gpgsign", "false")):
            git(seed, "config", key, value)
        (seed / "README.md").write_text(name)
        git(seed, "add", ".")
        git(seed, "commit", "-q", "-m", "init")
        git(seed, "push", "-q", "origin", "main")
        for branch in branches:
            git(seed, "push", "-q", "origin", f"main:{branch}")
        shutil.rmtree(seed)
        return bare

    return make


@pytest.fixture
def workspace_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point the workspace cache and workspaces dir at tmp; return the workspaces dir."""
    workspaces = tmp_path / "workspaces"
    monkeypatch.setenv("UNTAPED_WORKSPACE__CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("UNTAPED_WORKSPACE__WORKSPACES_DIR", str(workspaces))
    get_settings.cache_clear()
    yield workspaces
    get_settings.cache_clear()


def commit_in(worktree: Path, name: str = "change.txt") -> None:
    for key, value in (("user.email", "t@t"), ("user.name", "t"), ("commit.gpgsign", "false")):
        git(worktree, "config", key, value)
    (worktree / name).write_text("x")
    git(worktree, "add", name)
    git(worktree, "commit", "-q", "-m", f"add {name}")
