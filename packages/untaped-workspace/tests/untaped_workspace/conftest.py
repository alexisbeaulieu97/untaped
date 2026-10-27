"""Real-git fixtures: throwaway upstream repos, and the store and workspaces dir under tmp.

Every test runs as a workspace command would, under a run of workspace and
git: the store and the state rows read their settings from it.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from untaped.sdk.testing.git import GitRemote, git_remote
from workspace_test_support import composed


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


@pytest.fixture(autouse=True)
def workspace_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point the repo store and the workspaces dir at tmp, under a run; the workspaces dir."""
    workspaces = tmp_path / "workspaces"
    monkeypatch.setenv("UNTAPED_GIT__STORE_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("UNTAPED_WORKSPACE__WORKSPACES_DIR", str(workspaces))
    with composed():
        yield workspaces


@pytest.fixture
def store_root(tmp_path: Path) -> Path:
    """The repo store's root (``git.store_dir``)."""
    return tmp_path / "store"
