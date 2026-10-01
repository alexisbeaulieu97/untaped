"""``--scm-branch HEAD``: the current branch, only once HEAD is pushed to its upstream."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from untaped.capabilities.awx.infrastructure.git_head import pushed_branch
from untaped.sdk import ConfigError


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )
    return done.stdout.strip()


@pytest.fixture
def clone(tmp_path: Path) -> Path:
    """A clone whose ``work`` branch tracks ``origin/feature/x``, both at one commit."""
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "--initial-branch=main", str(bare)], check=True)
    repo = tmp_path / "clone"
    subprocess.run(["git", "clone", str(bare), str(repo)], check=True, capture_output=True)
    for key, value in [
        ("user.email", "t@t"),
        ("user.name", "t"),
        ("commit.gpgsign", "false"),
    ]:
        _git(repo, "config", key, value)
    _git(repo, "checkout", "-b", "work")
    _git(repo, "commit", "--allow-empty", "-m", "one")
    _git(repo, "push", "-u", "origin", "work:feature/x")
    return repo


def test_a_pushed_branch_is_named_as_on_its_remote(clone: Path) -> None:
    assert pushed_branch(clone) == "feature/x"


def test_unpushed_commits_are_refused(clone: Path) -> None:
    _git(clone, "commit", "--allow-empty", "-m", "two")
    with pytest.raises(
        ConfigError, match=r"is not pushed: origin feature/x is at [0-9a-f]{12}"
    ) as caught:
        pushed_branch(clone)
    # The checkout needs fixing (push it): an environment problem in git.
    assert (caught.value.category, caught.value.system) == ("config", "git")


def test_a_branch_without_upstream_is_refused(clone: Path) -> None:
    _git(clone, "checkout", "-b", "local")
    with pytest.raises(ConfigError, match="branch 'local' has no upstream"):
        pushed_branch(clone)


def test_a_deleted_remote_branch_is_refused(clone: Path) -> None:
    _git(clone, "push", "origin", "--delete", "feature/x")
    with pytest.raises(ConfigError, match="origin has no feature/x"):
        pushed_branch(clone)


def test_a_detached_head_is_refused(clone: Path) -> None:
    _git(clone, "checkout", "--detach")
    with pytest.raises(ConfigError, match="HEAD is detached"):
        pushed_branch(clone)


def test_outside_a_repository_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="--scm-branch HEAD") as caught:
        pushed_branch(tmp_path)
    assert caught.value.system == "git"


def test_a_branch_named_like_a_tag_still_resolves(clone: Path) -> None:
    _git(clone, "tag", "work")
    assert pushed_branch(clone) == "feature/x"


def test_a_local_upstream_is_refused(clone: Path) -> None:
    _git(clone, "checkout", "-b", "topic", "--track", "work")
    with pytest.raises(ConfigError, match="tracks local branch work"):
        pushed_branch(clone)
