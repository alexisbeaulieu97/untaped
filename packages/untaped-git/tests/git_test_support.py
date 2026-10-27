"""Helpers the git plugin's tests share: store handles, and git run on a store repo."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from untaped_git.errors import GitError
from untaped_git.store import RepoStore

type StoreFor = Callable[..., RepoStore]


class StoreError(GitError):
    """The error class the tests' store handles raise; a consumer passes its own."""


def ensure(store: RepoStore) -> bool:
    """Create the repo and reconcile its policy, as every writer does first; created?"""
    with store._locked():
        return store._ensure()


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
