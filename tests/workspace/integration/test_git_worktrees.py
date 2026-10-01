"""LocalGitWorktrees against real local git repositories."""

from __future__ import annotations

import os
import shutil
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from untaped.capabilities.workspace.errors import GitError, WorkspaceError
from untaped.capabilities.workspace.infrastructure import LocalGitWorktrees
from untaped.capabilities.workspace.infrastructure.bare_cache import cache_path_for
from workspace.conftest import add_submodule, commit_in, git, init_submodules

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


def _advance_origin(url: str, branch: str, clone: Path) -> None:
    """Push one new commit (``upstream.txt``) to ``branch`` from a separate clone."""
    git(clone.parent, "clone", "-q", "-b", branch, url, str(clone))
    commit_in(clone, "upstream.txt")
    git(clone, "push", "-q", "origin", branch)


def test_in_use_remote_branch_is_a_conflict(
    worktrees: LocalGitWorktrees, make_upstream: Callable[..., Path], tmp_path: Path
) -> None:
    url = str(make_upstream("api", branches=("feature/x",)))
    worktrees.checkout(url, tmp_path / "a" / "api", branch="feature/x", base=None)
    with pytest.raises(GitError) as caught:
        worktrees.checkout(url, tmp_path / "b" / "api", branch="feature/x", base=None)
    assert caught.value.category == "conflict"
    assert caught.value.hint


def test_legacy_mirror_heads_are_not_resumed(
    worktrees: LocalGitWorktrees, make_upstream: Callable[..., Path], tmp_path: Path
) -> None:
    from untaped.capabilities.workspace.infrastructure.bare_cache import cache_path_for

    upstream = make_upstream("api", branches=("feat",))
    url = str(upstream)
    cache = cache_path_for(url, cache_dir=tmp_path / "cache")
    cache.parent.mkdir(parents=True)
    git(tmp_path, "clone", "-q", "--bare", url, str(cache))  # an old-style mirror cache
    git(upstream, "branch", "-D", "feat")
    checkout = worktrees.checkout(url, tmp_path / "ws" / "api", branch="feat", base=None)
    assert (checkout.action, checkout.detail) == ("created", "from origin/main")


def test_resume_fast_forwards_a_branch_behind_origin(
    worktrees: LocalGitWorktrees, make_upstream: Callable[..., Path], tmp_path: Path
) -> None:
    url = str(make_upstream("api"))
    first = tmp_path / "a" / "api"
    worktrees.checkout(url, first, branch="b", base=None)
    commit_in(first)
    git(first, "push", "-q")
    worktrees.remove(url, first, force=False)
    _advance_origin(url, "b", tmp_path / "other")
    second = tmp_path / "b" / "api"
    checkout = worktrees.checkout(url, second, branch="b", base=None)
    assert checkout.detail == "tracking origin/b"
    assert (second / "upstream.txt").exists()
    status = worktrees.status(second, branch="b", base="main")
    assert status is not None and status.unpushed == 0


def test_resume_a_branch_ahead_of_origin(
    worktrees: LocalGitWorktrees, make_upstream: Callable[..., Path], tmp_path: Path
) -> None:
    url = str(make_upstream("api", branches=("b",)))
    first = tmp_path / "a" / "api"
    worktrees.checkout(url, first, branch="b", base=None)
    commit_in(first)
    worktrees.remove(url, first, force=False)
    second = tmp_path / "b" / "api"
    checkout = worktrees.checkout(url, second, branch="b", base=None)
    assert checkout.detail == "resumed; ahead of origin/b"
    assert (second / "change.txt").exists()
    status = worktrees.status(second, branch="b", base="main")
    assert status is not None and status.unpushed == 1


def test_resume_a_branch_diverged_from_origin(
    worktrees: LocalGitWorktrees, make_upstream: Callable[..., Path], tmp_path: Path
) -> None:
    url = str(make_upstream("api", branches=("b",)))
    first = tmp_path / "a" / "api"
    worktrees.checkout(url, first, branch="b", base=None)
    commit_in(first)
    worktrees.remove(url, first, force=False)
    _advance_origin(url, "b", tmp_path / "other")
    second = tmp_path / "b" / "api"
    checkout = worktrees.checkout(url, second, branch="b", base=None)
    assert checkout.detail == "resumed; diverged from origin/b"
    assert (second / "change.txt").exists()


def test_half_initialised_cache_is_repaired(
    worktrees: LocalGitWorktrees, make_upstream: Callable[..., Path], tmp_path: Path
) -> None:
    from untaped.capabilities.workspace.infrastructure.bare_cache import cache_path_for

    url = str(make_upstream("api"))
    cache = cache_path_for(url, cache_dir=tmp_path / "cache")
    cache.parent.mkdir(parents=True)
    git(tmp_path, "init", "-q", "--bare", str(cache))  # crashed before `remote add`
    checkout = worktrees.checkout(url, tmp_path / "ws" / "api", branch="x", base=None)
    assert checkout.action == "created"
    assert git(cache, "config", "remote.origin.url") == url


def test_hand_deleted_destination_can_be_checked_out_again(
    worktrees: LocalGitWorktrees, make_upstream: Callable[..., Path], tmp_path: Path
) -> None:
    import shutil

    url = str(make_upstream("api"))
    dest = tmp_path / "ws" / "api"
    worktrees.checkout(url, dest, branch="b", base=None)
    shutil.rmtree(dest)
    worktrees.checkout(url, dest, branch="b", base=None)
    assert (dest / "README.md").exists()


def test_read_only_commits_count_as_unpushed(
    worktrees: LocalGitWorktrees, make_upstream: Callable[..., Path], tmp_path: Path
) -> None:
    url = str(make_upstream("api"))
    dest = tmp_path / "ws" / "api"
    worktrees.checkout(url, dest, branch=None, base=None)
    commit_in(dest)
    status = worktrees.status(dest, branch=None, base="main")
    assert status is not None and status.unpushed == 1


def test_unpushed_is_counted_when_the_base_is_gone_from_origin(
    worktrees: LocalGitWorktrees, make_upstream: Callable[..., Path], tmp_path: Path
) -> None:
    upstream = make_upstream("api", branches=("dev",))
    url = str(upstream)
    dest = tmp_path / "ws" / "api"
    worktrees.checkout(url, dest, branch="b", base="dev")
    commit_in(dest)
    git(upstream, "branch", "-D", "dev")
    worktrees.fetch(url)
    status = worktrees.status(dest, branch="b", base="dev")
    assert status is not None and status.unpushed == 1


def test_never_pushed_branch_has_no_upstream(
    worktrees: LocalGitWorktrees, make_upstream: Callable[..., Path], tmp_path: Path
) -> None:
    url = str(make_upstream("api"))
    dest = tmp_path / "ws" / "api"
    worktrees.checkout(url, dest, branch="b", base=None)
    status = worktrees.status(dest, branch="b", base="main")
    assert status is not None and status.upstream is None
    git(dest, "push", "-q", "origin", "b")
    pushed = worktrees.status(dest, branch="b", base="main")
    assert pushed is not None and pushed.upstream == "origin/b"


def test_initialised_submodules_are_reported_and_force_removed(
    worktrees: LocalGitWorktrees, make_upstream: Callable[..., Path], tmp_path: Path
) -> None:
    upstream = make_upstream("api")
    add_submodule(upstream, make_upstream("lib"))
    url = str(upstream)
    dest = tmp_path / "ws" / "api"
    worktrees.checkout(url, dest, branch="b", base=None)
    before = worktrees.status(dest, branch="b", base="main")
    assert before is not None and before.submodules is False  # not initialised yet
    init_submodules(dest)
    status = worktrees.status(dest, branch="b", base="main")
    assert status is not None and status.submodules is True
    worktrees.remove(url, dest, force=True)
    assert not dest.exists()


@pytest.mark.parametrize("other", ["api", "web"])
def test_status_of_an_unregistered_worktree_raises_git_error(
    worktrees: LocalGitWorktrees, make_upstream: Callable[..., Path], tmp_path: Path, other: str
) -> None:
    """A recreated cache leaves the old worktree orphaned, or (same dir name) aliased."""
    url = str(make_upstream("api"))
    dest = tmp_path / "a" / "api"
    worktrees.checkout(url, dest, branch="a", base=None)
    cache = cache_path_for(url, cache_dir=tmp_path / "cache")
    cache.rename(cache.with_name("moved.git"))
    worktrees.checkout(url, tmp_path / "b" / other, branch="b", base=None)  # a fresh cache
    with pytest.raises(GitError) as caught:
        worktrees.status(dest, branch="a", base="main")
    if other == "api":  # aliased: the admin dir exists but points at another worktree
        assert caught.value.hint is not None and "git worktree repair" in caught.value.hint
    with pytest.raises(GitError):
        worktrees.remove(url, dest, force=False)
    assert dest.exists()
    worktrees.remove(url, dest, force=True)
    assert not dest.exists()


def test_status_accepts_a_relative_admin_gitdir(
    worktrees: LocalGitWorktrees,
    make_upstream: Callable[..., Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """git 2.48+ with worktree.useRelativePaths writes the admin gitdir relative to it."""
    url = str(make_upstream("api"))
    dest = tmp_path / "ws" / "api"
    worktrees.checkout(url, dest, branch="b", base=None)
    admin = Path(git(dest, "rev-parse", "--absolute-git-dir"))
    (admin / "gitdir").write_text(os.path.relpath(dest / ".git", admin) + "\n")
    monkeypatch.chdir(tmp_path)
    status = worktrees.status(dest, branch="b", base="main")
    assert status is not None and status.branch == "b"


def test_force_remove_reports_a_directory_it_cannot_delete(
    worktrees: LocalGitWorktrees,
    make_upstream: Callable[..., Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = str(make_upstream("api"))
    dest = tmp_path / "ws" / "api"
    worktrees.checkout(url, dest, branch="b", base=None)
    shutil.rmtree(cache_path_for(url, cache_dir=tmp_path / "cache"))

    def refuse(path: object, *args: object, **kwargs: object) -> None:
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(shutil, "rmtree", refuse)
    with pytest.raises(WorkspaceError) as caught:
        worktrees.remove(url, dest, force=True)
    assert (caught.value.category, caught.value.system) == ("failed", "local")
    assert str(dest) in str(caught.value)


def test_an_existing_origin_head_is_not_refreshed_when_no_base_is_given(
    worktrees: LocalGitWorktrees, make_upstream: Callable[..., Path], tmp_path: Path
) -> None:
    upstream = make_upstream("api", branches=("dev",))
    url = str(upstream)
    worktrees.checkout(url, tmp_path / "a" / "api", branch="a", base=None)
    git(upstream, "symbolic-ref", "HEAD", "refs/heads/dev")
    checkout = worktrees.checkout(url, tmp_path / "b" / "api", branch="b", base=None)
    assert checkout.base == "main"  # origin/HEAD was already known; no extra remote query


def test_an_existing_directory_hint_never_suggests_deleting(
    worktrees: LocalGitWorktrees, make_upstream: Callable[..., Path], tmp_path: Path
) -> None:
    url = str(make_upstream("api"))
    dest = tmp_path / "ws" / "api"
    dest.mkdir(parents=True)
    (dest / "notes.md").write_text("keep")
    with pytest.raises(GitError) as caught:
        worktrees.checkout(url, dest, branch="b", base=None)
    assert caught.value.category == "conflict"
    assert caught.value.hint == "move the existing directory aside, or use another branch name"
    assert (dest / "notes.md").exists()
