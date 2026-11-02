"""The plain-clone layout: any plugin declares it, one per repo holds it, release frees it."""

from __future__ import annotations

from pathlib import Path

import pytest

from git_test_support import StoreError, StoreFor, all_refs, git
from untaped.sdk import ErrorCategory
from untaped_git.plain_clone import HOLDER
from untaped_git.release import Released, Removed
from untaped_git.testing import GitRemote


def _holder(repo: Path) -> str | None:
    """The plugin the repo records as holding its plain-clone refs."""
    for line in git(repo, "config", "--local", "--list").splitlines():
        key, _, value = line.partition("=")
        if key == HOLDER.lower():
            return value
    return None


def test_any_plugin_that_declares_it_gets_the_plain_clone_layout(
    remote: GitRemote, store_for: StoreFor
) -> None:
    remote.tag("v1")
    trees = store_for("trees", plain_clone=True)

    trees.fetch(branches=["*"], tags=["*"], prune=True)

    refs = all_refs(trees.path)
    assert "refs/remotes/origin/main" in refs
    assert "refs/tags/v1" in refs
    assert not any(ref.startswith("refs/untaped/") for ref in refs)
    assert git(trees.path, "symbolic-ref", "refs/remotes/origin/HEAD").strip() == (
        "refs/remotes/origin/main"
    )
    assert _holder(trees.path) == "trees"
    assert trees.refs() == {"heads/main": remote.oid("main"), "tags/v1": remote.oid("v1")}


def test_a_plugin_that_does_not_declare_it_gets_its_own_namespace_whatever_its_name(
    store_for: StoreFor,
) -> None:
    store = store_for("workspace")

    store.fetch(branches=["main"])

    assert "refs/untaped/workspace/heads/main" in all_refs(store.path)
    assert _holder(store.path) is None


def test_a_second_plugin_declaring_it_is_refused_while_the_first_holds_refs(
    store_for: StoreFor,
) -> None:
    store_for("trees", plain_clone=True).fetch(branches=["main"])
    other = store_for("other", plain_clone=True)

    with pytest.raises(StoreError, match="are trees's") as raised:
        other.fetch(branches=["main"])
    with pytest.raises(StoreError, match="are trees's"):
        other.delete_refs(["heads/main"])

    assert raised.value.category is ErrorCategory.CONFLICT
    assert "refs/remotes/origin/main" in all_refs(other.path)
    assert _holder(other.path) == "trees"
    # A plugin in its own namespace shares the repo as before.
    store_for("other").fetch(branches=["main"])


def test_a_holder_with_nothing_left_in_the_repo_is_replaced(store_for: StoreFor) -> None:
    trees = store_for("trees", plain_clone=True)
    trees.fetch(branches=["main"])
    trees.delete_refs(["heads/main"])

    store_for("other", plain_clone=True).fetch(branches=["main"])

    assert _holder(trees.path) == "other"


def test_a_holder_keeping_a_worktree_still_holds(store_for: StoreFor, tmp_path: Path) -> None:
    trees = store_for("trees", plain_clone=True)
    trees.fetch(branches=["main"])
    trees.worktree_add(tmp_path / "one", "refs/remotes/origin/main", branch="one")
    trees.delete_refs(["heads/main"])

    with pytest.raises(StoreError, match="are trees's"):
        store_for("other", plain_clone=True).fetch(branches=["main"])


def test_release_frees_the_layout_for_another_plugin(
    remote: GitRemote, store_for: StoreFor, tmp_path: Path
) -> None:
    remote.tag("v1")
    store_for("github").fetch(branches=["main"])
    trees = store_for("trees", plain_clone=True)
    trees.fetch(branches=["*"], tags=["*"], prune=True)
    trees.worktree_add(tmp_path / "one", "refs/remotes/origin/main")

    outcome = trees.release()

    assert outcome == Released(plugins={"github": 0})
    refs = all_refs(trees.path)
    assert not any(ref.startswith(("refs/remotes/", "refs/tags/")) for ref in refs)
    assert _holder(trees.path) is None
    store_for("other", plain_clone=True).fetch(branches=["main"])
    assert _holder(trees.path) == "other"


def test_the_record_decides_whose_plain_refs_a_release_deletes(store_for: StoreFor) -> None:
    store_for("trees", plain_clone=True).fetch(branches=["main"])
    store_for("github").fetch(branches=["main"])

    # The holder lets go through a handle that didn't declare the layout: its refs still go.
    outcome = store_for("trees").release()

    assert outcome == Released(plugins={"github": 0})
    assert "refs/remotes/origin/main" not in all_refs(store_for("github").path)


def test_another_plugin_releasing_leaves_the_holders_refs(store_for: StoreFor) -> None:
    trees = store_for("trees", plain_clone=True)
    trees.fetch(branches=["main"])
    other = store_for("other", plain_clone=True)

    outcome = other.release()

    assert outcome == Released(plugins={"trees": 0})
    assert "refs/remotes/origin/main" in all_refs(trees.path)
    assert _holder(trees.path) == "trees"


def test_plain_refs_nobody_recorded_keep_the_repo(store_for: StoreFor) -> None:
    github = store_for("github")
    github.fetch(branches=["main"])
    main = all_refs(github.path)["refs/untaped/github/heads/main"]
    git(github.path, "update-ref", "refs/tags/by-hand", main)

    outcome = github.release()

    assert outcome == Released(refs=("refs/tags/by-hand",))
    assert outcome.kept() == "ref refs/tags/by-hand"
    assert github.exists()


def test_with_everything_gone_the_repo_goes(store_for: StoreFor) -> None:
    trees = store_for("trees", plain_clone=True)
    trees.fetch(branches=["main"])

    assert isinstance(trees.release(), Removed)
