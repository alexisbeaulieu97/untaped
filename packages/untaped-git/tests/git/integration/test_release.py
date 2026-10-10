"""Release: a plugin lets go of a store repo; the repo goes when nobody else holds it."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from git.conftest import StoreFor, all_refs, git
from untaped.sdk import ErrorCategory
from untaped.testing.git import GitRemote
from untaped_git.domain.release import Released, Removed
from untaped_git.errors import StoreError
from untaped_git.infrastructure.repo_files import worktree_entries
from untaped_git.infrastructure.store import RELEASE_MARK, RepoStore


def _shared(store_for: StoreFor, tmp_path: Path) -> tuple[Path, Path]:
    """Github, ansible and workspace (two worktrees) in one repo; returns the worktrees."""
    github = store_for("github")
    github.fetch(branches=["main"])
    github.private_file.write_text("{}")
    ansible = store_for("ansible")
    ansible.fetch(branches=["main"], tags=["*"])
    ansible.private_file.write_text("{}")
    workspace = store_for("workspace")
    workspace.fetch(branches=["*"], tags=["*"], prune=True)
    workspace.private_file.write_text("{}")
    trees = (tmp_path / "ws" / "one", tmp_path / "ws" / "two")
    workspace.worktree_add(trees[0], "refs/remotes/origin/main", branch="one")
    workspace.worktree_add(trees[1], "refs/remotes/origin/main", branch="two")
    return trees


def test_release_keeps_a_shared_repo_and_deletes_only_its_own(
    remote: GitRemote, store_for: StoreFor, tmp_path: Path
) -> None:
    remote.tag("v1")
    trees = _shared(store_for, tmp_path)
    github = store_for("github")

    outcome = github.release()

    assert outcome == Released(plugins={"ansible": 0, "workspace": 2}, branches=("one", "two"))
    assert outcome.kept() == "ansible, workspace (2 worktrees), branches one, two"
    refs = all_refs(github.path)
    assert not any(ref.startswith("refs/untaped/github/") for ref in refs)
    assert "refs/untaped/ansible/heads/main" in refs
    assert "refs/remotes/origin/main" in refs
    assert not github.private_file.exists()
    assert store_for("ansible").private_file.exists()
    assert git(trees[0], "status", "--short", bare=False) == ""
    unmarked = subprocess.run(
        ["git", f"--git-dir={github.path}", "config", RELEASE_MARK], capture_output=True
    )
    assert unmarked.returncode == 1


def test_release_removes_a_repo_only_that_plugin_used(store_for: StoreFor) -> None:
    github = store_for("github")
    github.fetch(branches=["main"])
    github.private_file.write_text("{}")

    outcome = github.release()

    assert isinstance(outcome, Removed)
    assert outcome.freed_bytes > 0
    assert not github.path.exists()
    assert not github.path.with_name("app.git.removing").exists()


def test_has_part_names_a_file_refs_or_its_own_interrupted_release(
    remote: GitRemote, store_for: StoreFor
) -> None:
    github, ansible = store_for("github"), store_for("ansible")
    assert not github.has_part()  # no repo yet
    github.fetch(branches=["main"])
    assert github.has_part()  # its refs
    assert not ansible.has_part()  # another plugin's repo
    ansible.private_file.write_text("{}")
    assert ansible.has_part()
    ansible.private_file.unlink()
    git(github.path, "config", RELEASE_MARK, "github")
    assert not ansible.has_part()  # someone else's mark
    git(github.path, "config", RELEASE_MARK, "ansible")
    assert ansible.has_part()  # its own release, interrupted after its refs went


def test_release_of_a_missing_repo_frees_nothing(store_for: StoreFor) -> None:
    assert store_for("github").release() == Removed(0)


def test_a_worktree_added_by_hand_keeps_the_repo_and_is_named(
    store_for: StoreFor, tmp_path: Path
) -> None:
    github = store_for("github")
    github.fetch(branches=["main"])
    hand = tmp_path / "scratch" / "tool-wt"
    git(
        github.path,
        "worktree",
        "add",
        "--quiet",
        "--detach",
        str(hand),
        "refs/untaped/github/heads/main",
    )

    outcome = github.release()

    assert isinstance(outcome, Released)
    assert outcome.foreign_worktrees == (str(hand),)
    assert outcome.kept() == f"1 worktree not untaped's ({hand})"
    assert hand.is_dir()


def test_release_removes_the_plugins_own_worktrees(store_for: StoreFor, tmp_path: Path) -> None:
    github = store_for("github")
    github.fetch(branches=["main"])
    tree = tmp_path / "plugins" / "github" / "worktrees" / "app"
    github.worktree_add(tree, "refs/untaped/github/heads/main")

    outcome = github.release()

    assert isinstance(outcome, Removed)
    assert not tree.exists()


def test_workspace_release_deletes_its_plain_clone_refs_and_named_branches(
    remote: GitRemote, store_for: StoreFor, tmp_path: Path
) -> None:
    remote.tag("v1")
    github = store_for("github")
    github.fetch(branches=["main"])
    workspace = store_for("workspace")
    workspace.fetch(branches=["*"], tags=["*"], prune=True)
    tree = tmp_path / "ws" / "one"
    workspace.worktree_add(tree, "refs/remotes/origin/main", branch="fix")
    git(workspace.path, "worktree", "remove", "--force", str(tree))

    outcome = workspace.release(branches=["fix"])

    assert outcome == Released(plugins={"github": 0})
    refs = all_refs(workspace.path)
    assert not any(ref.startswith(("refs/remotes/", "refs/tags/", "refs/heads/")) for ref in refs)


def test_a_branch_left_behind_keeps_the_repo_without_a_mark(
    store_for: StoreFor, tmp_path: Path
) -> None:
    workspace = store_for("workspace")
    workspace.fetch(branches=["*"], tags=["*"], prune=True)
    tree = tmp_path / "ws" / "one"
    workspace.worktree_add(tree, "refs/remotes/origin/main", branch="fix")
    git(workspace.path, "worktree", "remove", "--force", str(tree))

    outcome = workspace.release()

    assert outcome == Released(branches=("fix",))
    assert outcome.kept() == "branch fix"
    before = all_refs(workspace.path)
    # S39: no mark, so another consumer's ensure() finishes nothing and deletes nothing.
    assert store_for("github").ensure() is False
    assert all_refs(workspace.path) == before


def test_a_checked_out_branch_is_refused_before_anything_changes(
    store_for: StoreFor, tmp_path: Path
) -> None:
    workspace = store_for("workspace")
    workspace.fetch(branches=["*"], tags=["*"], prune=True)
    workspace.private_file.write_text("{}")
    workspace.worktree_add(tmp_path / "ws" / "one", "refs/remotes/origin/main", branch="fix")
    before = all_refs(workspace.path)

    with pytest.raises(StoreError, match="fix checked out") as caught:
        workspace.release(branches=["fix"])

    assert caught.value.category == ErrorCategory.CONFLICT
    assert all_refs(workspace.path) == before
    assert workspace.private_file.exists()


@pytest.mark.parametrize("name", ["*", "a..b", "-x", "refs/heads/x"])
def test_release_refuses_a_name_that_is_not_a_branch(store_for: StoreFor, name: str) -> None:
    with pytest.raises(StoreError) as caught:
        store_for("workspace").release(branches=[name])
    assert caught.value.category == ErrorCategory.INVALID


def test_ensure_finishes_an_interrupted_release_and_keeps_the_others(
    remote: GitRemote, store_for: StoreFor
) -> None:
    ansible = store_for("ansible")
    ansible.fetch(branches=["main"])
    github = store_for("github")
    github.fetch(branches=["main"])
    github.private_file.write_text("{}")
    git(github.path, "config", RELEASE_MARK, "github")  # killed right after the mark

    assert ansible.ensure() is False

    refs = all_refs(github.path)
    assert "refs/untaped/ansible/heads/main" in refs
    assert not any(ref.startswith("refs/untaped/github/") for ref in refs)
    assert not github.private_file.exists()
    assert "untaped.release" not in (github.path / "config").read_text()


def test_ensure_recreates_a_repo_whose_interrupted_release_left_nobody(
    remote: GitRemote, store_for: StoreFor
) -> None:
    github = store_for("github")
    github.fetch(branches=["main"])
    git(github.path, "config", RELEASE_MARK, "github")
    ansible = store_for("ansible")

    assert ansible.ensure() is True

    assert not any(ref.startswith("refs/untaped/") for ref in all_refs(ansible.path))
    ansible.fetch(branches=["main"])
    assert ansible.refs() == {"heads/main": remote.oid("main")}


def test_a_removing_directory_is_deleted_by_ensure_and_by_the_leftover_sweep(
    store_for: StoreFor,
) -> None:
    github = store_for("github")
    removing = github.path.with_name("app.git.removing")
    (removing / "objects").mkdir(parents=True)

    github.ensure()
    assert not removing.exists()

    (removing / "objects").mkdir(parents=True)
    github.fetch(branches=["main"])
    assert not removing.exists()


def test_owned_by_lists_repos_holding_the_private_file(
    store_for: StoreFor, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_GIT__STORE_DIR", str(tmp_path / "store"))
    github = store_for("github")
    github.fetch(branches=["main"])
    github.private_file.write_text("{}")
    store_for("ansible").fetch(branches=["main"])
    (found,) = RepoStore.owned_by("github", error=StoreError)

    assert found.path == github.path
    assert found.private_file == github.private_file
    assert RepoStore.owned_by("ansible", error=StoreError) == []


def test_a_worktree_added_from_an_owned_one_stays_the_users(
    store_for: StoreFor, tmp_path: Path
) -> None:
    """git copies config.worktree into it, owner included; the path stamp says it isn't ours."""
    workspace = store_for("workspace")
    workspace.fetch(branches=["*"], tags=["*"], prune=True)
    owned = tmp_path / "ws" / "one"
    workspace.worktree_add(owned, "refs/remotes/origin/main", branch="one")
    hand = tmp_path / "scratch" / "copy"
    git(owned, "worktree", "add", "--quiet", "--detach", str(hand), bare=False)

    assert workspace.worktree_owners() == {owned.resolve(): "workspace", hand: None}
    outcome = workspace.release()

    assert isinstance(outcome, Released)
    assert outcome.foreign_worktrees == (str(hand),)
    assert hand.is_dir()
    assert not owned.exists()


def test_release_finishes_another_plugins_interrupted_release_first(
    store_for: StoreFor,
) -> None:
    github = store_for("github")
    github.fetch(branches=["main"])
    github.private_file.write_text("{}")
    git(github.path, "config", RELEASE_MARK, "github")  # killed right after the mark
    ansible = store_for("ansible")
    ansible.fetch(branches=["main"])

    outcome = ansible.release()

    assert isinstance(outcome, Removed)
    assert not github.path.exists()


def test_release_unless_in_use_keeps_everything_while_a_worktree_is_registered(
    store_for: StoreFor, tmp_path: Path
) -> None:
    workspace = store_for("workspace")
    workspace.fetch(branches=["*"], tags=["*"], prune=True)
    workspace.private_file.write_text("{}")
    tree = tmp_path / "ws" / "one"
    workspace.worktree_add(tree, "refs/remotes/origin/main", branch="one")
    before = all_refs(workspace.path)

    outcome = workspace.release(branches=["one"], unless_in_use=True)

    assert outcome == Released(plugins={"workspace": 1}, branches=("one",))
    assert all_refs(workspace.path) == before
    assert workspace.private_file.exists()
    assert tree.is_dir()


def test_a_worktree_registered_with_a_relative_path_is_still_owned(
    store_for: StoreFor, tmp_path: Path
) -> None:
    """git 2.48+ ``worktree.useRelativePaths`` writes the admin ``gitdir`` relative."""
    github = store_for("github")
    github.fetch(branches=["main"])
    tree = tmp_path / "plugins" / "github" / "worktrees" / "app"
    github.worktree_add(tree, "refs/untaped/github/heads/main")
    (admin,) = (github.path / "worktrees").iterdir()
    target = os.path.relpath(tree.resolve() / ".git", admin.resolve())
    (admin / "gitdir").write_text(target + "\n")

    assert github.worktree_owners() == {tree.resolve(): "github"}
    assert isinstance(github.release(), Removed)
    assert not tree.exists()


def test_a_relative_worktree_path_resolves_through_a_symlinked_store(
    store_for: StoreFor, tmp_path: Path
) -> None:
    """git computes the relative path between real paths; ``..`` must not apply to the link."""
    github = store_for("github")
    github.fetch(branches=["main"])
    tree = tmp_path / "plugins" / "github" / "worktrees" / "app"
    github.worktree_add(tree, "refs/untaped/github/heads/main")
    deep = tmp_path / "real" / "a" / "b"
    deep.mkdir(parents=True)
    moved = deep / github.path.name
    github.path.rename(moved)
    link = tmp_path / "link"
    link.symlink_to(deep)
    (admin,) = ((link / github.path.name) / "worktrees").iterdir()
    target = os.path.relpath(tree.resolve() / ".git", admin.resolve())
    (admin / "gitdir").write_text(target + "\n")

    (entry,) = worktree_entries(link / github.path.name)

    assert entry.path == tree.resolve()
    assert entry.owner == "github"
