"""LocalGitWorktrees on the repo store, against real local git repositories."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from untaped_workspace.domain import LocalBranch, StoredRepo, StoreUse, archive_blockers
from untaped_workspace.errors import GitError, WorkspaceError
from untaped_workspace.infrastructure import LocalGitWorktrees
from workspace.conftest import add_submodule, commit_in, git, init_submodules, store_repo


@pytest.fixture
def worktrees(store_root: Path) -> LocalGitWorktrees:
    return LocalGitWorktrees()


def test_new_branch_from_the_default_base(
    worktrees: LocalGitWorktrees, make_upstream: Callable[..., Path], tmp_path: Path
) -> None:
    url = str(make_upstream("api"))
    dest = tmp_path / "ws" / "api"
    checkout = worktrees.checkout(url, dest, branch="feature/x", base=None)
    assert (checkout.action, checkout.base, checkout.backfill_error) == ("created", "main", None)
    assert git(dest, "rev-parse", "--abbrev-ref", "HEAD") == "feature/x"
    assert git(dest, "config", "branch.feature/x.merge") == "refs/heads/feature/x"


def test_a_checkout_lands_in_the_store_with_workspaces_file(
    worktrees: LocalGitWorktrees,
    make_upstream: Callable[..., Path],
    store_root: Path,
    tmp_path: Path,
) -> None:
    url = str(make_upstream("api"))
    dest = tmp_path / "ws" / "api"
    worktrees.checkout(url, dest, branch="b", base=None)
    repo = store_repo(store_root, url)
    assert json.loads((repo / "untaped-workspace.json").read_text()) == {"history": "complete"}
    assert git(dest, "config", "--worktree", "untaped.owner") == "workspace"
    assert git(dest, "rev-parse", "--git-common-dir") == str(repo)
    assert worktrees.in_store(url)


def test_existing_remote_branch_is_tracked(
    worktrees: LocalGitWorktrees, make_upstream: Callable[..., Path], tmp_path: Path
) -> None:
    url = str(make_upstream("api", branches=("feature/x",)))
    dest = tmp_path / "ws" / "api"
    checkout = worktrees.checkout(url, dest, branch="feature/x", base=None)
    assert (checkout.action, checkout.detail) == ("checked_out", "tracking origin/feature/x")
    assert git(dest, "rev-parse", "--abbrev-ref", "@{upstream}") == "origin/feature/x"


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
    status = worktrees.status(dest, branch="feature/x")
    assert status is not None
    assert (status.branch, status.untracked, status.unpushed) == ("feature/x", 1, 1)


def test_status_of_a_missing_worktree_is_none(worktrees: LocalGitWorktrees, tmp_path: Path) -> None:
    assert worktrees.status(tmp_path / "gone", branch="b") is None


def test_sibling_workspace_stashes_are_not_counted(
    worktrees: LocalGitWorktrees, make_upstream: Callable[..., Path], tmp_path: Path
) -> None:
    url = str(make_upstream("api"))
    mine, sibling = tmp_path / "a" / "api", tmp_path / "b" / "api"
    worktrees.checkout(url, mine, branch="mine", base=None)
    worktrees.checkout(url, sibling, branch="theirs", base=None)
    _stash_a_change(sibling)
    status = worktrees.status(mine, branch="mine")
    assert status is not None and status.stashed == 0
    theirs = worktrees.status(sibling, branch="theirs")
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
    url = str(make_upstream("api"))
    dest = tmp_path / "ws" / "api"
    worktrees.checkout(url, dest, branch="b", base=None)
    shutil.rmtree(dest)
    worktrees.remove(url, dest, force=False)
    worktrees.checkout(url, tmp_path / "ws2" / "api", branch="b", base=None)  # branch is free again


def test_remove_without_a_store_repo_needs_force(
    worktrees: LocalGitWorktrees, make_upstream: Callable[..., Path], tmp_path: Path
) -> None:
    url = str(make_upstream("api"))
    dest = tmp_path / "ws" / "api"
    dest.mkdir(parents=True)
    with pytest.raises(GitError) as caught:
        worktrees.remove(url, dest, force=False)
    assert caught.value.hint == "pass --force to delete the directory"
    worktrees.remove(url, dest, force=True)
    assert not dest.exists()


def test_a_renamed_default_branch_is_picked_up(
    worktrees: LocalGitWorktrees, make_upstream: Callable[..., Path], tmp_path: Path
) -> None:
    upstream = make_upstream("api")
    url = str(upstream)
    worktrees.checkout(url, tmp_path / "a" / "api", branch="a", base=None)
    git(upstream, "branch", "-m", "main", "trunk")  # origin renames its default branch
    checkout = worktrees.checkout(url, tmp_path / "b" / "api", branch="b", base=None)
    assert checkout.base == "trunk"


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
    status = worktrees.status(second, branch="b")
    assert status is not None and status.unpushed == 0


def test_resume_a_branch_ahead_of_origin(
    worktrees: LocalGitWorktrees, make_upstream: Callable[..., Path], tmp_path: Path
) -> None:
    url = str(make_upstream("api", branches=("b",)))
    first = tmp_path / "a" / "api"
    worktrees.checkout(url, first, branch="b", base=None)
    commit_in(first)
    worktrees.remove(url, first, force=True)  # unpushed: the branch keeps it
    second = tmp_path / "b" / "api"
    checkout = worktrees.checkout(url, second, branch="b", base=None)
    assert checkout.detail == "resumed; ahead of origin/b"
    assert (second / "change.txt").exists()
    status = worktrees.status(second, branch="b")
    assert status is not None and status.unpushed == 1


def test_resume_a_branch_diverged_from_origin(
    worktrees: LocalGitWorktrees, make_upstream: Callable[..., Path], tmp_path: Path
) -> None:
    url = str(make_upstream("api", branches=("b",)))
    first = tmp_path / "a" / "api"
    worktrees.checkout(url, first, branch="b", base=None)
    commit_in(first)
    worktrees.remove(url, first, force=True)  # unpushed: the branch keeps it
    _advance_origin(url, "b", tmp_path / "other")
    second = tmp_path / "b" / "api"
    checkout = worktrees.checkout(url, second, branch="b", base=None)
    assert checkout.detail == "resumed; diverged from origin/b"
    assert (second / "change.txt").exists()


def test_a_half_created_store_repo_is_repaired(
    worktrees: LocalGitWorktrees,
    make_upstream: Callable[..., Path],
    store_root: Path,
    tmp_path: Path,
) -> None:
    url = str(make_upstream("api"))
    repo = store_repo(store_root, url)
    repo.parent.mkdir(parents=True)
    git(tmp_path, "init", "-q", "--bare", str(repo))  # crashed before the label was written
    checkout = worktrees.checkout(url, tmp_path / "ws" / "api", branch="x", base=None)
    assert checkout.action == "created"
    assert git(repo, "config", "remote.origin.url") == url


def test_hand_deleted_destination_can_be_checked_out_again(
    worktrees: LocalGitWorktrees, make_upstream: Callable[..., Path], tmp_path: Path
) -> None:
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
    status = worktrees.status(dest, branch=None)
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
    assert worktrees.fetch(url, dest) is None
    status = worktrees.status(dest, branch="b")
    assert status is not None and status.unpushed == 1


def test_fetch_without_a_store_repo_does_nothing(
    worktrees: LocalGitWorktrees, make_upstream: Callable[..., Path], tmp_path: Path
) -> None:
    url = str(make_upstream("api"))
    assert worktrees.fetch(url, tmp_path / "ws" / "api") is None
    assert not worktrees.in_store(url)


def test_never_pushed_branch_has_no_upstream(
    worktrees: LocalGitWorktrees, make_upstream: Callable[..., Path], tmp_path: Path
) -> None:
    url = str(make_upstream("api"))
    dest = tmp_path / "ws" / "api"
    worktrees.checkout(url, dest, branch="b", base=None)
    status = worktrees.status(dest, branch="b")
    assert status is not None and status.upstream is None
    git(dest, "push", "-q", "origin", "b")
    pushed = worktrees.status(dest, branch="b")
    assert pushed is not None and pushed.upstream == "origin/b"


def test_initialised_submodules_are_reported_and_force_removed(
    worktrees: LocalGitWorktrees, make_upstream: Callable[..., Path], tmp_path: Path
) -> None:
    upstream = make_upstream("api")
    add_submodule(upstream, make_upstream("lib"))
    url = str(upstream)
    dest = tmp_path / "ws" / "api"
    worktrees.checkout(url, dest, branch="b", base=None)
    before = worktrees.status(dest, branch="b")
    assert before is not None and before.submodules is False  # not initialised yet
    init_submodules(dest)
    status = worktrees.status(dest, branch="b")
    assert status is not None and status.submodules is True
    worktrees.remove(url, dest, force=True)
    assert not dest.exists()


@pytest.mark.parametrize("other", ["api", "web"])
def test_status_of_an_unregistered_worktree_raises_git_error(
    worktrees: LocalGitWorktrees,
    make_upstream: Callable[..., Path],
    store_root: Path,
    tmp_path: Path,
    other: str,
) -> None:
    """A recreated store repo leaves the old worktree orphaned, or (same dir name) aliased."""
    url = str(make_upstream("api"))
    dest = tmp_path / "a" / "api"
    worktrees.checkout(url, dest, branch="a", base=None)
    repo = store_repo(store_root, url)
    repo.rename(repo.with_name("moved.git"))
    worktrees.checkout(url, tmp_path / "b" / other, branch="b", base=None)  # a fresh store repo
    with pytest.raises(GitError) as caught:
        worktrees.status(dest, branch="a")
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
    status = worktrees.status(dest, branch="b")
    assert status is not None and status.branch == "b"


def test_force_remove_reports_a_directory_it_cannot_delete(
    worktrees: LocalGitWorktrees,
    make_upstream: Callable[..., Path],
    store_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = str(make_upstream("api"))
    dest = tmp_path / "ws" / "api"
    worktrees.checkout(url, dest, branch="b", base=None)
    shutil.rmtree(store_repo(store_root, url))

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


def _stash_a_change(worktree: Path) -> None:
    for key, value in (("user.email", "t@t"), ("user.name", "t")):
        git(worktree, "config", key, value)
    (worktree / "README.md").write_text("changed")
    git(worktree, "stash", "push", "-q")


@pytest.mark.parametrize(
    "change",
    [commit_in, _stash_a_change, lambda dest: (dest / "scratch.txt").write_text("x")],
    ids=["commit", "stash", "untracked"],
)
def test_remove_rechecks_for_work_made_after_the_status_check(
    worktrees: LocalGitWorktrees,
    make_upstream: Callable[..., Path],
    tmp_path: Path,
    change: Callable[[Path], object],
) -> None:
    url = str(make_upstream("api"))
    dest = tmp_path / "ws" / "api"
    worktrees.checkout(url, dest, branch="b", base=None)
    assert archive_blockers(worktrees.status(dest, branch="b")) == ()
    change(dest)  # after the check, before the removal
    with pytest.raises(GitError) as caught:
        worktrees.remove(url, dest, force=False)
    assert (caught.value.category, caught.value.system) == ("conflict", "git")
    assert caught.value.hint == "the repo changed since the check; run status and archive again"
    assert (dest / "README.md").exists()
    assert git(dest, "rev-parse", "--abbrev-ref", "HEAD") == "b"


def test_remote_branches_lists_the_stored_origin_branches(
    worktrees: LocalGitWorktrees, make_upstream: Callable[..., Path], tmp_path: Path
) -> None:
    url = str(make_upstream("api", branches=("release/2",)))
    assert worktrees.remote_branches(url) == []
    worktrees.checkout(url, tmp_path / "ws" / "api", branch=None, base=None)
    assert worktrees.remote_branches(url) == ["main", "release/2"]


# -- stored repos (the picker's list) ---------------------------------------


def _plant(root: Path, relative: str, origin: str | None, *, used: bool = True) -> Path:
    repo = root / relative
    repo.parent.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "--bare", str(repo))
    if origin is not None:
        git(repo, "config", "remote.origin.url", origin)
    if used:
        (repo / "untaped-workspace.json").write_text('{"history": "complete"}\n')
    return repo


def test_stored_repos_lists_the_repos_workspace_used(
    worktrees: LocalGitWorktrees, store_root: Path
) -> None:
    assert worktrees.stored_repos() == []
    _plant(store_root, "github.com/team/tool.git", "git@github.com:team/tool.git")
    _plant(store_root, "github.com/grp/sub/repo.git", "https://github.com/grp/sub/repo.git")
    _plant(store_root, "git.example/project.git", "https://git.example/project.git")
    _plant(store_root, "gitlab.example/team/unlabelled.git", None)
    _plant(store_root, "gitlab.example/team/github-only.git", "https://gitlab.example/team/x")
    _plant(store_root, "gitlab.example/team/elsewhere.git", "https://gitlab.example/team/other")
    (store_root / "gitlab.example/team/github-only.git/untaped-workspace.json").unlink()
    _plant(store_root, "_unknown/0123456789abcdef.git", "/srv/somewhere.git")
    assert worktrees.stored_repos() == [
        StoredRepo(key=("git.example", "project.git"), origin="https://git.example/project.git"),
        StoredRepo(
            key=("github.com", "grp", "sub", "repo.git"),
            origin="https://github.com/grp/sub/repo.git",
        ),
        StoredRepo(key=("github.com", "team", "tool.git"), origin="git@github.com:team/tool.git"),
    ]
    assert worktrees.stored_repos()[0].ident == "git.example/project"


def test_stored_repos_runs_no_git(
    worktrees: LocalGitWorktrees, store_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("a", "b", "c"):
        _plant(store_root, f"github.com/team/{name}.git", f"https://github.com/team/{name}")

    def no_git(*args: object, **kwargs: object) -> None:
        raise AssertionError("a process ran")

    monkeypatch.setattr(subprocess, "Popen", no_git)
    monkeypatch.setattr(subprocess, "run", no_git)
    assert len(worktrees.stored_repos()) == 3


# -- remove's view of a store repo, and release --------------------------------


def test_store_use_names_branches_worktrees_unpushed_work_and_stashes(
    worktrees: LocalGitWorktrees,
    make_upstream: Callable[..., Path],
    store_root: Path,
    tmp_path: Path,
) -> None:
    url = str(make_upstream("api"))
    assert worktrees.store_use(url) is None
    live, pushed, ahead, stashed = (tmp_path / name / "api" for name in ("w", "x", "y", "z"))
    worktrees.checkout(url, live, branch="live", base=None)
    worktrees.checkout(url, pushed, branch="pushed", base=None)
    git(pushed, "push", "-q", "origin", "pushed")
    worktrees.checkout(url, ahead, branch="ahead", base=None)
    commit_in(ahead)
    worktrees.checkout(url, stashed, branch="stashed", base=None)
    _stash_a_change(stashed)
    for dest in (pushed, ahead, stashed):
        worktrees.remove(url, dest, force=True)
    hand = tmp_path / "hand"
    git(store_repo(store_root, url), "worktree", "add", "-q", "--detach", str(hand), "origin/main")

    assert worktrees.store_use(url) == StoreUse(
        branches=(
            LocalBranch(name="ahead", checked_out=False, unpushed=1, stashed=False),
            LocalBranch(name="live", checked_out=True, unpushed=0, stashed=False),
            LocalBranch(name="pushed", checked_out=False, unpushed=0, stashed=False),
            LocalBranch(name="stashed", checked_out=False, unpushed=0, stashed=True),
        ),
        worktrees=1,  # the hand-added one is nobody's
    )


def test_release_reports_who_kept_the_repo_or_what_it_freed(
    worktrees: LocalGitWorktrees,
    make_upstream: Callable[..., Path],
    store_root: Path,
    tmp_path: Path,
) -> None:
    url = str(make_upstream("api"))
    dest = tmp_path / "ws" / "api"
    worktrees.checkout(url, dest, branch="b", base=None)
    worktrees.remove(url, dest, force=False)

    kept = worktrees.release(url, branches=())
    assert (kept.action, kept.detail) == ("released", "kept: branch b")

    removed = worktrees.release(url, branches=["b"])
    assert removed.action == "removed"
    assert removed.freed_bytes > 0
    assert removed.detail.endswith(" freed")
    assert not store_repo(store_root, url).exists()
