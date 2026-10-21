"""A holder's worktrees are locked against pruning, repaired after a move and found by folder."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from git.conftest import StoreError, StoreFor, git
from untaped.sdk import ErrorCategory
from untaped.testing.git import GitRemote
from untaped_git.infrastructure.store import RepoStore

_MAIN = "refs/remotes/origin/main"


def _workspace(store_for: StoreFor) -> RepoStore:
    store = store_for("workspace")
    store.fetch(branches=["main"])
    return store


def _registered(store: RepoStore) -> list[str]:
    porcelain = git(store.path, "worktree", "list", "--porcelain")
    lines = porcelain.splitlines()
    return [line.removeprefix("worktree ") for line in lines if line.startswith("worktree ")]


def _lock_lines(store: RepoStore) -> list[str]:
    porcelain = git(store.path, "worktree", "list", "--porcelain")
    return [line for line in porcelain.splitlines() if line.startswith("locked")]


@pytest.mark.parametrize("gone", ["moved", "deleted"])
def test_a_locked_worktree_survives_prune_when_its_folder_goes(
    store_for: StoreFor, tmp_path: Path, gone: str
) -> None:
    store = _workspace(store_for)
    held = tmp_path / "PROJ-1" / "app"
    loose = tmp_path / "PROJ-2" / "app"
    store.worktree_add(held, _MAIN, branch="one", lock_reason="untaped project PROJ-1")
    store.worktree_add(loose, _MAIN, branch="two")

    for tree in (held, loose):
        if gone == "moved":
            tree.parent.rename(tmp_path / f"{tree.parent.name}-moved")
        else:
            shutil.rmtree(tree.parent)
    store.run(["worktree", "prune"])

    assert _registered(store)[1:] == [str(held.resolve())]
    assert _lock_lines(store) == ["locked untaped project PROJ-1"]
    assert store.worktrees_under(tmp_path / "PROJ-1") == [held.resolve()]


def test_a_release_still_removes_a_locked_worktree(store_for: StoreFor, tmp_path: Path) -> None:
    store = _workspace(store_for)
    tree = tmp_path / "PROJ-1" / "app"
    store.worktree_add(tree, _MAIN, branch="one", lock_reason="untaped project PROJ-1")
    shutil.rmtree(tree.parent)

    store.release()

    assert store.worktree_owners() == {}
    assert _lock_lines(store) == []


def test_a_bad_reason_is_refused_before_anything_is_added(
    store_for: StoreFor, tmp_path: Path
) -> None:
    store = _workspace(store_for)
    tree = tmp_path / "PROJ-1" / "app"

    for reason in ("", "  ", "two\nlines"):
        with pytest.raises(StoreError, match="lock reason") as caught:
            store.worktree_add(tree, _MAIN, branch="one", lock_reason=reason)
        assert caught.value.category == ErrorCategory.INVALID
    assert not tree.exists()
    assert _registered(store) == [str(store.path)]


def test_locking_an_existing_worktree_is_idempotent_and_keeps_the_first_reason(
    store_for: StoreFor, tmp_path: Path
) -> None:
    store = _workspace(store_for)
    tree = tmp_path / "ws" / "app"
    store.worktree_add(tree, _MAIN, branch="one")

    store.lock_worktree(tree, reason="untaped project PROJ-1")
    store.lock_worktree(tree, reason="untaped project PROJ-2")

    assert _lock_lines(store) == ["locked untaped project PROJ-1"]


def test_locking_or_repairing_a_users_own_clone_is_refused(
    remote: GitRemote, store_for: StoreFor, tmp_path: Path
) -> None:
    store = _workspace(store_for)
    clone = tmp_path / "mine"
    subprocess.run(["git", "clone", "--quiet", str(remote.path), str(clone)], check=True)

    for call in (
        lambda: store.lock_worktree(clone, reason="untaped project PROJ-1"),
        lambda: store.repair_worktree(clone),
    ):
        with pytest.raises(StoreError, match="is not a worktree of repo store") as caught:
            call()
        assert caught.value.category == ErrorCategory.INVALID
    assert not (clone / ".git" / "locked").exists()


def test_repair_reconnects_a_moved_worktree_and_restores_its_owner_stamp(
    store_for: StoreFor, tmp_path: Path
) -> None:
    store = _workspace(store_for)
    old = tmp_path / "PROJ-1"
    store.worktree_add(old / "app", _MAIN, branch="one", lock_reason="untaped project PROJ-1")
    new = tmp_path / "moved" / "PROJ-1"
    new.parent.mkdir()
    old.rename(new)
    assert store.worktree_owners() == {(old / "app").resolve(): "workspace"}

    store.repair_worktree(new / "app")
    store.repair_worktree(new / "app")

    assert store.worktree_owners() == {(new / "app").resolve(): "workspace"}
    assert store.worktrees_under(new) == [(new / "app").resolve()]
    assert store.worktrees_under(old) == []
    assert _lock_lines(store) == ["locked untaped project PROJ-1"]
    assert git(new / "app", "rev-parse", "--abbrev-ref", "HEAD", bare=False).strip() == "one"


def test_repair_leaves_a_hand_added_worktrees_inherited_stamp_alone(
    store_for: StoreFor, tmp_path: Path
) -> None:
    store = _workspace(store_for)
    owned = tmp_path / "ws" / "app"
    store.worktree_add(owned, _MAIN, branch="one")
    mine = tmp_path / "mine"
    git(owned, "worktree", "add", "--quiet", "-b", "two", str(mine), "one", bare=False)
    moved = tmp_path / "mine-moved"
    mine.rename(moved)

    store.repair_worktree(moved)

    assert store.worktree_owners() == {owned.resolve(): "workspace", moved.resolve(): None}


def test_worktrees_under_lists_only_this_plugins_entries_inside_the_folder(
    store_for: StoreFor, tmp_path: Path
) -> None:
    workspace = _workspace(store_for)
    sweep = store_for("sweep")
    sweep.fetch(branches=["main"])
    project = tmp_path / "PROJ-1"
    workspace.worktree_add(project / "api", _MAIN, branch="one")
    workspace.worktree_add(project / "nested" / "web", _MAIN, branch="two")
    workspace.worktree_add(tmp_path / "PROJ-10" / "api", _MAIN, branch="three")
    sweep.worktree_add(project / "other", sweep.ref("heads/main"))
    git(workspace.path, "worktree", "add", "--quiet", str(project / "hand"), "one^0")

    assert workspace.worktrees_under(project) == [
        (project / "api").resolve(),
        (project / "nested" / "web").resolve(),
    ]
    assert sweep.worktrees_under(project) == [(project / "other").resolve()]
