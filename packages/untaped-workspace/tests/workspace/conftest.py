"""Real-git fixtures: throwaway upstream repos and an isolated workspace config."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from untaped import bootstrap
from untaped.settings import get_settings
from untaped.testing import plugin_candidate
from untaped.testing.git import GitRemote, git_remote
from untaped_git import SPEC as GIT
from untaped_git.api import store_key
from untaped_workspace import SPEC as WORKSPACE


def _git_dir(cwd: Path) -> list[str]:
    """``--git-dir`` for a bare repository: the suite runs with ``safe.bareRepository=explicit``."""
    return (
        ["--git-dir", str(cwd)] if (cwd / "HEAD").is_file() and (cwd / "objects").is_dir() else []
    )


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *_git_dir(cwd), *args], cwd=cwd, text=True, capture_output=True, check=False
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.fixture
def make_upstream(tmp_path: Path) -> Callable[..., GitRemote]:
    """Create an upstream repo with one commit on ``main``, answering at
    ``https://git.example/acme/<name>.git`` (the test ``HOME``'s git config rewrites the URL to
    a local bare repo, so typed URLs pass ``GitUrl``); return it."""
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")

    def make(name: str = "api", *, branches: tuple[str, ...] = ()) -> GitRemote:
        remote = git_remote(tmp_path, f"acme/{name}")
        for branch in branches:
            remote.branch(branch)
        return remote

    return make


@pytest.fixture
def store_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point the repo store (``git.store_dir``) at tmp; return its root."""
    root = tmp_path / "store"
    monkeypatch.setenv("UNTAPED_GIT__STORE_DIR", str(root))
    get_settings.cache_clear()
    yield root
    get_settings.cache_clear()


@pytest.fixture
def composed(fresh_composition: None) -> None:
    """A root composed of the git and workspace plugins: a store fetch of a hosted URL asks
    the plugins filling ``GitHost`` (none here) for credentials."""
    bootstrap.compose_root(candidates=[plugin_candidate(GIT), plugin_candidate(WORKSPACE)])


def store_repo(root: Path, url: str) -> Path:
    """The store repo of ``url`` under the store ``root``."""
    return root.joinpath(*store_key(url))


@pytest.fixture
def workspace_env(
    store_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """Point the repo store and workspaces dir at tmp; return the workspaces dir."""
    workspaces = tmp_path / "workspaces"
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


def add_submodule(upstream: GitRemote, sub: GitRemote, *, path: str = "lib") -> None:
    """Push a commit adding ``sub`` as a submodule at ``path`` to ``upstream``'s ``main``."""
    seed = upstream.path.parent / f"_seed_sub_{upstream.path.stem}"
    git(upstream.path.parent, "clone", "-q", str(upstream.path), str(seed))
    for key, value in (("user.email", "t@t"), ("user.name", "t"), ("commit.gpgsign", "false")):
        git(seed, "config", key, value)
    git(seed, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub.path), path)
    git(seed, "commit", "-q", "-m", "add submodule")
    git(seed, "push", "-q", "origin", "main")
    shutil.rmtree(seed)


def init_submodules(worktree: Path) -> None:
    git(worktree, "-c", "protocol.file.allow=always", "submodule", "update", "-q", "--init")
