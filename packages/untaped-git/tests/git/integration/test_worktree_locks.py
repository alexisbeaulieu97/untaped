"""A holder's worktrees are locked against pruning, repaired after a move and found by folder."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from git.conftest import StoreError, StoreFor, git
from untaped.sdk import ErrorCategory
from untaped.sdk.testing.git import GitRemote
from untaped_git.infrastructure.helper_nonce import helper_nonce
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

    for reason in ("", "  ", "two\nlines", "nul\0byte"):
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


def test_repair_keeps_the_owner_after_git_already_repaired_the_move(
    store_for: StoreFor, tmp_path: Path
) -> None:
    store = _workspace(store_for)
    old = tmp_path / "PROJ-1" / "app"
    store.worktree_add(old, _MAIN, branch="one", lock_reason="untaped project PROJ-1")
    new = tmp_path / "PROJ-1-moved" / "app"
    old.parent.rename(new.parent)
    git(new, "worktree", "repair", bare=False)

    store.repair_worktree(new)

    assert store.worktree_owners() == {new.resolve(): "workspace"}


def test_a_missing_folders_entry_can_still_be_locked(store_for: StoreFor, tmp_path: Path) -> None:
    store = _workspace(store_for)
    tree = tmp_path / "PROJ-1" / "app"
    store.worktree_add(tree, _MAIN, branch="one")
    shutil.rmtree(tree.parent)

    store.lock_worktree(tree, reason="untaped project PROJ-1")
    store.run(["worktree", "prune"])

    assert _lock_lines(store) == ["locked untaped project PROJ-1"]
    with pytest.raises(StoreError, match="is not a worktree of repo store"):
        store.lock_worktree(tmp_path / "PROJ-2" / "app", reason="untaped project PROJ-2")


def test_relative_paths_name_the_callers_folder(
    store_for: StoreFor, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _workspace(store_for)
    for project in ("PROJ-1", "PROJ-2"):
        store.worktree_add(tmp_path / project / "api", _MAIN, branch=project)
    monkeypatch.chdir(tmp_path)

    store.lock_worktree(Path("PROJ-2/api"), reason="untaped project PROJ-2")
    Path("PROJ-2").rename("PROJ-3")
    store.repair_worktree(Path("PROJ-3/api"))

    porcelain = git(store.path, "worktree", "list", "--porcelain")
    assert f"worktree {(tmp_path / 'PROJ-3' / 'api').resolve()}\nHEAD" in porcelain
    assert _lock_lines(store) == ["locked untaped project PROJ-2"]
    assert store.worktrees_under(Path("PROJ-3")) == [(tmp_path / "PROJ-3" / "api").resolve()]


def test_worktrees_under_resolves_a_symlinked_folder(store_for: StoreFor, tmp_path: Path) -> None:
    store = _workspace(store_for)
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real")
    store.worktree_add(tmp_path / "link" / "PROJ-1" / "app", _MAIN, branch="one")

    assert store.worktrees_under(tmp_path / "link" / "PROJ-1") == [
        (tmp_path / "real" / "PROJ-1" / "app").resolve()
    ]


@pytest.mark.parametrize("by_hand", [False, True])
def test_a_hand_added_worktree_moved_with_its_source_stays_the_users(
    store_for: StoreFor, tmp_path: Path, by_hand: bool
) -> None:
    store = _workspace(store_for)
    old = tmp_path / "P1"
    store.worktree_add(old / "app", _MAIN, branch="one")
    git(old / "app", "worktree", "add", "--quiet", "-b", "two", str(old / "mine"), bare=False)
    new = tmp_path / "P2"
    old.rename(new)
    if by_hand:
        for tree in ("app", "mine"):
            git(new / tree, "worktree", "repair", bare=False)

    store.repair_worktree(new / "app")
    store.repair_worktree(new / "mine")

    assert store.worktree_owners() == {
        (new / "app").resolve(): "workspace",
        (new / "mine").resolve(): None,
    }


def test_a_copy_of_a_copy_reusing_the_owned_entrys_id_stays_the_users(
    store_for: StoreFor, tmp_path: Path
) -> None:
    store = _workspace(store_for)
    owned = tmp_path / "P1" / "app"
    store.worktree_add(owned, _MAIN, branch="one")
    copy = tmp_path / "b"
    git(owned, "worktree", "add", "--quiet", "-b", "two", str(copy), bare=False)
    git(store.path, "worktree", "remove", "--force", str(owned))
    again = tmp_path / "other" / "app"
    git(copy, "worktree", "add", "--quiet", "-b", "three", str(again), bare=False)
    moved = tmp_path / "moved" / "app"
    again.parent.rename(moved.parent)
    git(moved, "worktree", "repair", bare=False)

    store.repair_worktree(moved)

    assert store.worktree_owners()[moved.resolve()] is None


def test_repair_gives_untapeds_helper_line_the_moved_worktrees_nonce(
    store_for: StoreFor, tmp_path: Path
) -> None:
    store = _workspace(store_for)
    old = tmp_path / "PROJ-1"
    store.worktree_add(old / "app", _MAIN, branch="one")
    key = "credential.https://git.example.com.helper"
    git(old / "app", "config", "--worktree", "--add", key, "", bare=False)
    git(
        old / "app",
        "config",
        "--worktree",
        "--add",
        key,
        "!untaped --profile work git credential --nonce 0123abcd",
        bare=False,
    )
    new = tmp_path / "moved" / "PROJ-1"
    new.parent.mkdir()
    old.rename(new)

    store.repair_worktree(new / "app")

    nonce = helper_nonce(store.path, new / "app")
    lines = git(new / "app", "config", "--worktree", "--get-all", key, bare=False).splitlines()
    assert lines == ["", f"!untaped --profile work git credential --nonce {nonce}"]
