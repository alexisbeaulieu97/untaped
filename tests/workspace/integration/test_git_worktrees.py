"""LocalGitWorktrees against real local git repositories."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from untaped.capabilities.workspace.errors import GitError
from untaped.capabilities.workspace.infrastructure import LocalGitWorktrees
from workspace.conftest import commit_in, git

pytestmark = pytest.mark.integration


@pytest.fixture
def worktrees(tmp_path: Path) -> LocalGitWorktrees:
    return LocalGitWorktrees(tmp_path / "cache")


def test_new_branch_from_the_default_base(
    worktrees: LocalGitWorktrees, make_upstream: Callable[..., Path], tmp_path: Path
) -> None:
    url = str(make_upstream("api"))
    dest = tmp_path / "ws" / "api"
    checkout = worktrees.checkout(url, dest, branch="feature/x", base=None)
    assert (checkout.action, checkout.base) == ("created", "main")
    assert git(dest, "rev-parse", "--abbrev-ref", "HEAD") == "feature/x"
    assert git(dest, "config", "branch.feature/x.merge") == "refs/heads/feature/x"


def test_existing_remote_branch_is_tracked(
    worktrees: LocalGitWorktrees, make_upstream: Callable[..., Path], tmp_path: Path
) -> None:
    url = str(make_upstream("api", branches=("feature/x",)))
    checkout = worktrees.checkout(url, tmp_path / "ws" / "api", branch="feature/x", base=None)
    assert (checkout.action, checkout.detail) == ("checked_out", "tracking origin/feature/x")


def test_read_only_is_detached_at_the_base(
    worktrees: LocalGitWorktrees, make_upstream: Callable[..., Path], tmp_path: Path
) -> None:
    url = str(make_upstream("api"))
    dest = tmp_path / "ws" / "api"
    worktrees.checkout(url, dest, branch=None, base=None)
    assert git(dest, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"


def test_missing_base_is_not_found(
    worktrees: LocalGitWorktrees, make_upstream: Callable[..., Path], tmp_path: Path
) -> None:
    url = str(make_upstream("api"))
    with pytest.raises(GitError) as caught:
        worktrees.checkout(url, tmp_path / "ws" / "api", branch="x", base="nope")
    assert caught.value.category == "not_found"


def test_branch_already_in_another_workspace_is_a_conflict(
    worktrees: LocalGitWorktrees, make_upstream: Callable[..., Path], tmp_path: Path
) -> None:
    url = str(make_upstream("api"))
    worktrees.checkout(url, tmp_path / "a" / "api", branch="feature/x", base=None)
    with pytest.raises(GitError) as caught:
        worktrees.checkout(url, tmp_path / "b" / "api", branch="feature/x", base=None)
    assert caught.value.category == "conflict"
    assert caught.value.hint


def test_resume_after_removal_keeps_local_commits(
    worktrees: LocalGitWorktrees, make_upstream: Callable[..., Path], tmp_path: Path
) -> None:
    url = str(make_upstream("api"))
    first = tmp_path / "a" / "api"
    worktrees.checkout(url, first, branch="feature/x", base=None)
    commit_in(first)
    worktrees.remove(url, first, force=True)
    second = tmp_path / "b" / "api"
    checkout = worktrees.checkout(url, second, branch="feature/x", base=None)
    assert checkout.detail == "resumed local branch"
    assert (second / "change.txt").exists()


def test_status_counts_and_unpushed(
    worktrees: LocalGitWorktrees, make_upstream: Callable[..., Path], tmp_path: Path
) -> None:
    url = str(make_upstream("api"))
    dest = tmp_path / "ws" / "api"
    worktrees.checkout(url, dest, branch="feature/x", base=None)
    commit_in(dest)
    (dest / "new.txt").write_text("y")
    status = worktrees.status(dest, branch="feature/x", base="main")
    assert status is not None
    assert (status.branch, status.untracked, status.unpushed) == ("feature/x", 1, 1)


def test_status_of_a_missing_worktree_is_none(worktrees: LocalGitWorktrees, tmp_path: Path) -> None:
    assert worktrees.status(tmp_path / "gone", branch="b", base="main") is None


def test_sibling_workspace_stashes_are_not_counted(
    worktrees: LocalGitWorktrees, make_upstream: Callable[..., Path], tmp_path: Path
) -> None:
    url = str(make_upstream("api"))
    mine, sibling = tmp_path / "a" / "api", tmp_path / "b" / "api"
    worktrees.checkout(url, mine, branch="mine", base=None)
    worktrees.checkout(url, sibling, branch="theirs", base=None)
    for key, value in (("user.email", "t@t"), ("user.name", "t")):
        git(sibling, "config", key, value)
    (sibling / "README.md").write_text("changed")
    git(sibling, "stash", "push", "-q")
    status = worktrees.status(mine, branch="mine", base="main")
    assert status is not None and status.stashed == 0
    theirs = worktrees.status(sibling, branch="theirs", base="main")
    assert theirs is not None and theirs.stashed == 1


def test_parallel_checkouts_of_one_repo(
    worktrees: LocalGitWorktrees, make_upstream: Callable[..., Path], tmp_path: Path
) -> None:
    url = str(make_upstream("api"))
    errors: list[BaseException] = []

    def run(name: str) -> None:
        try:
            worktrees.checkout(url, tmp_path / name / "api", branch=name, base=None)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(f"w{i}",)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert all((tmp_path / f"w{i}" / "api" / "README.md").exists() for i in range(4))


def test_remove_prunes_a_hand_deleted_worktree(
    worktrees: LocalGitWorktrees, make_upstream: Callable[..., Path], tmp_path: Path
) -> None:
    import shutil

    url = str(make_upstream("api"))
    dest = tmp_path / "ws" / "api"
    worktrees.checkout(url, dest, branch="b", base=None)
    shutil.rmtree(dest)
    worktrees.remove(url, dest, force=False)
    worktrees.checkout(url, tmp_path / "ws2" / "api", branch="b", base=None)  # branch is free again


def test_existing_cache_gets_the_remote_tracking_refspec(
    worktrees: LocalGitWorktrees, make_upstream: Callable[..., Path], tmp_path: Path
) -> None:
    from untaped.capabilities.workspace.infrastructure.bare_cache import cache_path_for

    url = str(make_upstream("api"))
    cache = cache_path_for(url, cache_dir=tmp_path / "cache")
    cache.parent.mkdir(parents=True)
    git(tmp_path, "clone", "-q", "--bare", url, str(cache))  # an old-style mirror cache
    worktrees.checkout(url, tmp_path / "ws" / "api", branch="b", base=None)
    assert (
        git(cache, "config", "--get-all", "remote.origin.fetch")
        == "+refs/heads/*:refs/remotes/origin/*"
    )
