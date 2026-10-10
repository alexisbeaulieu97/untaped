"""``adopt`` moves a 10.x bare repo into the store; the overlap rule keeps one copy either way."""

from __future__ import annotations

import errno
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from git.conftest import all_refs, git
from untaped.sdk import ErrorCategory
from untaped.testing.git import GitRemote
from untaped_git.api import adopt, remove_if_emptied
from untaped_git.errors import StoreError


def _store_repo() -> Path:
    return Path.home() / ".untaped" / "plugins" / "git" / "store" / "git.example" / "app.git"


def _github_10x(remote: GitRemote, root: Path) -> tuple[Path, Path]:
    """A 10.x sweep cache repo (shallow, heads and tags mirrored) and one sweep worktree."""
    repo = root / "git.example" / "app.git"
    repo.parent.mkdir(parents=True)
    subprocess.run(
        ["git", "clone", "--quiet", "--bare", "--depth=1", remote.url, str(repo)], check=True
    )
    (repo / "untaped-corpus.json").write_text(
        json.dumps({"profile": "default", "fetched_at": "x", "pushed_at": "y"}), encoding="utf-8"
    )
    worktree = root / "worktrees" / "sweep-1"
    git(repo, "worktree", "add", "--quiet", "--detach", str(worktree), "main")
    (root / "git.example" / "app.git.lock").write_text("", encoding="utf-8")
    return repo, worktree


def _workspace_10x(remote: GitRemote, root: Path, tree: Path) -> Path:
    """A 10.x workspace cache repo (full, shared refspec, layout mark) and a worktree on ``fix``."""
    repo = root / "git.example" / "app.git"
    repo.parent.mkdir(parents=True)
    git(Path("."), "init", "--quiet", "--bare", str(repo))
    git(repo, "remote", "add", "origin", remote.url)
    git(repo, "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*")
    git(repo, "config", "untaped.layout", "2")
    git(repo, "fetch", "--quiet", "origin")
    git(repo, "worktree", "add", "--quiet", "-b", "fix", str(tree), "refs/remotes/origin/main")
    return repo


def test_a_github_repo_moves_with_its_refs_renamed_and_worktrees_moved(
    remote: GitRemote, tmp_path: Path
) -> None:
    remote.tag("v1")
    root = tmp_path / "github-cache"
    source, worktree = _github_10x(remote, root)
    new_root = tmp_path / "plugins" / "github" / "worktrees"

    adopted = adopt(
        source, plugin="github", error=StoreError, worktrees=(root / "worktrees", new_root)
    )

    target = _store_repo()
    assert (adopted.repo, adopted.action) == (target, "moved")
    assert not source.exists()
    assert all_refs(target) == {
        "refs/untaped/github/heads/main": remote.oid("main"),
        "refs/untaped/github/tags/v1": remote.oid("v1"),
    }
    moved = new_root / "sweep-1"
    assert not worktree.exists()
    assert git(moved, "rev-parse", "HEAD", bare=False).strip() == remote.oid("main")
    assert git(moved, "config", "--worktree", "untaped.owner", bare=False).strip() == "github"
    assert git(target, "config", "untaped.store").strip()
    assert remove_if_emptied(root)


def test_a_workspace_repo_keeps_its_layout_and_loses_the_shared_refspec(
    remote: GitRemote, tmp_path: Path
) -> None:
    tree = tmp_path / "workspaces" / "task" / "app"
    source = _workspace_10x(remote, tmp_path / "workspace-cache", tree)
    hand_added = tmp_path / "mine"
    git(
        source,
        "worktree",
        "add",
        "--quiet",
        "--detach",
        str(hand_added),
        "refs/remotes/origin/main",
    )

    adopt(source, plugin="workspace", error=StoreError, owned=[tree])

    target = _store_repo()
    refs = all_refs(target)
    assert refs["refs/remotes/origin/main"] == remote.oid("main")
    assert refs["refs/heads/fix"] == remote.oid("main")
    shared = subprocess.run(
        ["git", f"--git-dir={target}", "config", "--local", "--get-all", "remote.origin.fetch"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert shared.stdout == ""
    assert "untaped.layout" not in git(target, "config", "--local", "--list")
    assert git(tree, "config", "--worktree", "untaped.owner", bare=False).strip() == "workspace"
    assert git(tree, "status", "--short", bare=False) == ""
    assert git(hand_added, "rev-parse", "HEAD", bare=False).strip() == remote.oid("main")


@pytest.mark.parametrize("github_first", [True, False])
def test_the_overlap_keeps_the_copy_holding_work_in_either_order(
    remote: GitRemote, tmp_path: Path, github_first: bool
) -> None:
    github, sweep = _github_10x(remote, tmp_path / "github-cache")
    tree = tmp_path / "workspaces" / "task" / "app"
    workspace = _workspace_10x(remote, tmp_path / "workspace-cache", tree)
    (github / "untaped-corpus.json").rename(github / "untaped-github.json")
    new_root = tmp_path / "plugins" / "github" / "worktrees"

    def adopt_github() -> str:
        moves = (tmp_path / "github-cache" / "worktrees", new_root)
        return adopt(github, plugin="github", error=StoreError, worktrees=moves).action

    def adopt_workspace() -> str:
        return adopt(workspace, plugin="workspace", error=StoreError, owned=[tree]).action

    if github_first:
        actions = [adopt_github(), adopt_workspace()]
        assert actions == ["moved", "replaced"]
    else:
        actions = [adopt_workspace(), adopt_github()]
        assert actions == ["moved", "dropped"]

    target = _store_repo()
    assert not github.exists() and not workspace.exists()
    assert not (target / "shallow").exists()
    assert all_refs(target)["refs/heads/fix"] == remote.oid("main")
    assert not any(ref.startswith("refs/untaped/github/") for ref in all_refs(target))
    copied = json.loads((target / "untaped-github.json").read_text(encoding="utf-8"))
    assert copied == {"profile": "default"}
    assert not sweep.exists() and not (new_root / "sweep-1").exists()
    assert git(tree, "status", "--short", bare=False) == ""


def test_both_copies_holding_work_is_a_conflict_that_changes_nothing(
    remote: GitRemote, tmp_path: Path
) -> None:
    first = _workspace_10x(remote, tmp_path / "a", tmp_path / "ws-a" / "app")
    adopt(first, plugin="workspace", error=StoreError, owned=[tmp_path / "ws-a" / "app"])
    second = _workspace_10x(remote, tmp_path / "b", tmp_path / "ws-b" / "app")
    before = all_refs(second)

    with pytest.raises(StoreError, match="both hold work") as caught:
        adopt(second, plugin="workspace", error=StoreError)

    assert caught.value.category == ErrorCategory.CONFLICT
    assert all_refs(second) == before
    assert git(tmp_path / "ws-b" / "app", "status", "--short", bare=False) == ""


def test_a_repo_without_an_origin_is_refused(tmp_path: Path) -> None:
    repo = tmp_path / "orphan.git"
    git(Path("."), "init", "--quiet", "--bare", str(repo))

    with pytest.raises(StoreError, match="has no origin URL"):
        adopt(repo, plugin="github", error=StoreError)
    assert repo.is_dir()


def test_an_emptied_root_goes_and_one_holding_anything_else_stays(tmp_path: Path) -> None:
    root = tmp_path / "cache"
    (root / "host" / "owner").mkdir(parents=True)
    (root / "host" / "owner" / "repo.git.lock").write_text("", encoding="utf-8")
    assert remove_if_emptied(root)
    assert not root.exists()

    kept = tmp_path / "kept"
    (kept / "host" / "stuck.git" / "refs" / "heads").mkdir(parents=True)
    (kept / "host" / "other.git.lock").write_text("", encoding="utf-8")
    assert not remove_if_emptied(kept)
    assert (kept / "host" / "stuck.git" / "refs" / "heads").is_dir()
    assert not (kept / "host" / "other.git.lock").exists()


def test_a_worktree_deleted_by_hand_is_left_to_git_and_the_repo_still_moves(
    remote: GitRemote, tmp_path: Path
) -> None:
    root = tmp_path / "github-cache"
    source, worktree = _github_10x(remote, root)
    shutil.rmtree(worktree)

    adopted = adopt(
        source, plugin="github", error=StoreError, worktrees=(root / "worktrees", tmp_path / "new")
    )

    assert adopted.action == "moved"
    assert not (tmp_path / "new" / "sweep-1").exists()


def test_the_kept_copys_own_files_win_and_an_unreadable_one_is_skipped(
    remote: GitRemote, tmp_path: Path
) -> None:
    tree = tmp_path / "ws" / "app"
    adopt(_workspace_10x(remote, tmp_path / "a", tree), plugin="workspace", error=StoreError)
    target = _store_repo()
    (target / "untaped-workspace.json").write_text('{"history": "complete"}\n', encoding="utf-8")
    github, _ = _github_10x(remote, tmp_path / "github-cache")
    (github / "untaped-corpus.json").unlink()
    (github / "untaped-github.json").write_text("{not json", encoding="utf-8")
    (github / "untaped-workspace.json").write_text('{"history": "partial"}\n', encoding="utf-8")

    moves = (tmp_path / "github-cache" / "worktrees", tmp_path / "new")
    assert adopt(github, plugin="github", error=StoreError, worktrees=moves).action == "dropped"

    assert not (target / "untaped-github.json").exists()
    assert json.loads((target / "untaped-workspace.json").read_text()) == {"history": "complete"}


def test_a_move_across_filesystems_copies_then_deletes(
    remote: GitRemote, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _ = _github_10x(remote, tmp_path / "github-cache")
    real_rename = Path.rename

    def cross_device(self: Path, target: Path) -> Path:
        if self == source:
            raise OSError(18, "Invalid cross-device link")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", cross_device)

    assert adopt(source, plugin="github", error=StoreError).action == "moved"
    assert not source.exists()
    assert "refs/untaped/github/heads/main" in all_refs(_store_repo())


def test_a_move_that_fails_is_the_plugins_error(
    remote: GitRemote, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _ = _github_10x(remote, tmp_path / "github-cache")

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(Path, "rename", refuse)
    monkeypatch.setattr(shutil, "move", refuse)

    with pytest.raises(StoreError, match="could not move") as caught:
        adopt(source, plugin="github", error=StoreError)
    assert caught.value.category == ErrorCategory.FAILED
    assert source.is_dir()


def test_a_missing_root_counts_as_gone(tmp_path: Path) -> None:
    assert remove_if_emptied(tmp_path / "nowhere")


def test_a_store_directory_without_a_repository_is_refused(
    remote: GitRemote, tmp_path: Path
) -> None:
    source, _ = _github_10x(remote, tmp_path / "github-cache")
    (_store_repo() / "stray").mkdir(parents=True)

    with pytest.raises(StoreError, match="holds no repository") as caught:
        adopt(source, plugin="github", error=StoreError)

    assert caught.value.category == ErrorCategory.CONFLICT
    assert source.is_dir() and not (_store_repo() / "app.git").exists()


def test_a_copy_across_filesystems_that_fails_leaves_no_half_repository(
    remote: GitRemote, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _ = _github_10x(remote, tmp_path / "github-cache")
    real_rename = Path.rename

    def cross_device(self: Path, target: Path) -> Path:
        if self == source:
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_rename(self, target)

    def disk_full(src: Path, dst: Path, **_kwargs: object) -> None:
        Path(dst).mkdir(parents=True)
        (Path(dst) / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(Path, "rename", cross_device)
    monkeypatch.setattr(shutil, "copytree", disk_full)

    with pytest.raises(StoreError, match="No space left"):
        adopt(source, plugin="github", error=StoreError)

    assert source.is_dir()
    assert not _store_repo().exists()
    assert not _store_repo().with_name("app.git.adopting").exists()


def test_a_run_cut_short_before_the_repo_moved_is_finished_by_the_next(
    remote: GitRemote, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "github-cache"
    source, _ = _github_10x(remote, root)
    new_root = tmp_path / "plugins" / "github" / "worktrees"
    moves = (root / "worktrees", new_root)
    real_rename = Path.rename

    def crash(self: Path, target: Path) -> Path:
        if self == source:
            raise OSError(errno.EIO, "Input/output error")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", crash)
    with pytest.raises(StoreError):
        adopt(source, plugin="github", error=StoreError, worktrees=moves)
    monkeypatch.setattr(Path, "rename", real_rename)

    assert (new_root / "sweep-1").is_dir()  # the worktree moved first
    assert adopt(source, plugin="github", error=StoreError, worktrees=moves).action == "moved"

    moved = new_root / "sweep-1"
    assert git(moved, "rev-parse", "HEAD", bare=False).strip() == remote.oid("main")
    assert git(moved, "config", "--worktree", "untaped.owner", bare=False).strip() == "github"
    assert "refs/untaped/github/heads/main" in all_refs(_store_repo())


def test_a_store_repo_or_another_plugins_repo_is_refused(remote: GitRemote, tmp_path: Path) -> None:
    tree = tmp_path / "ws" / "app"
    workspace = _workspace_10x(remote, tmp_path / "workspace-cache", tree)

    with pytest.raises(StoreError, match="workspace's 10\\.x repository"):
        adopt(workspace, plugin="github", error=StoreError)

    adopt(workspace, plugin="workspace", error=StoreError, owned=[tree])
    copy = tmp_path / "copy.git"
    shutil.copytree(_store_repo(), copy, symlinks=True)
    with pytest.raises(StoreError, match="a repo store repository already"):
        adopt(copy, plugin="workspace", error=StoreError)


def test_a_hand_added_worktree_of_a_github_repo_is_work(remote: GitRemote, tmp_path: Path) -> None:
    tree = tmp_path / "ws" / "app"
    adopt(_workspace_10x(remote, tmp_path / "a", tree), plugin="workspace", error=StoreError)
    root = tmp_path / "github-cache"
    github, _ = _github_10x(remote, root)
    mine = tmp_path / "mine"
    git(github, "worktree", "add", "--quiet", "--detach", str(mine), "main")

    with pytest.raises(StoreError, match="both hold work"):
        adopt(
            github,
            plugin="github",
            error=StoreError,
            worktrees=(root / "worktrees", tmp_path / "n"),
        )
    assert git(mine, "rev-parse", "HEAD", bare=False).strip() == remote.oid("main")
