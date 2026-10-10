"""Fixtures for the git plugin: a local remote and store handles on a temp store root."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from untaped.testing.git import GitRemote, git_remote
from untaped_git.errors import StoreError
from untaped_git.infrastructure.store import RepoStore

type StoreFor = Callable[..., RepoStore]


@pytest.fixture
def remote(tmp_path: Path) -> GitRemote:
    return git_remote(tmp_path)


@pytest.fixture
def warnings() -> list[str]:
    return []


@pytest.fixture
def store_for(tmp_path: Path, remote: GitRemote, warnings: list[str]) -> StoreFor:
    """``store_for(plugin, url=None)``: a store handle on ``remote`` under a temp root."""
    root = tmp_path / "store"

    def build(plugin: str, *, url: str | None = None) -> RepoStore:
        return RepoStore(
            root / "git.example" / "app.git",
            url=url or remote.url,
            plugin=plugin,
            error=StoreError,
            warn=warnings.append,
            sleep=lambda _seconds: None,
        )

    return build


def git(repo: Path, *args: str, bare: bool = True, input: str | None = None) -> str:
    """Run git on ``repo`` (a bare repo by ``--git-dir``, else a worktree) and return stdout."""
    where = [f"--git-dir={repo}"] if bare else ["-C", str(repo)]
    result = subprocess.run(
        ["git", *where, *args], input=input, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout


def all_refs(repo: Path) -> dict[str, str]:
    out = git(repo, "for-each-ref", "--format=%(refname) %(objectname)")
    return dict(line.split(" ", 1) for line in out.splitlines())


def objects(repo: Path) -> dict[str, int]:
    """``count-objects -v`` as numbers: ``count`` is loose objects, ``packs`` pack files."""
    out = git(repo, "count-objects", "-v")
    return {
        key: int(value)
        for key, _, value in (line.partition(": ") for line in out.splitlines())
        if value.isdigit()
    }
