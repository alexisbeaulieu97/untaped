"""Worktree config follows the handle's URL, refuses foreign paths; the store heals and guards."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from git.conftest import StoreFor, all_refs, git
from untaped.sdk import ErrorCategory
from untaped.testing.git import GitRemote, global_config
from untaped_git.errors import StoreError


def test_a_new_url_spelling_replaces_the_old_rewrite(
    remote: GitRemote, store_for: StoreFor, tmp_path: Path
) -> None:
    old = store_for("workspace")
    old.fetch(branches=["main"])
    tree = tmp_path / "ws" / "app"
    old.worktree_add(tree, "refs/remotes/origin/main", branch="fix")
    old.write_worktree_config(tree, profile=None)

    new_url = "https://git.example/APP.git"
    global_config(f"url.file://{remote.path}.insteadOf", new_url, add=True)
    store_for("workspace", url=new_url).write_worktree_config(tree, profile=None)

    assert git(tree, "remote", "get-url", "origin", bare=False).strip() == f"file://{remote.path}"
    rewrites = git(tree, "config", "--worktree", "--get-regexp", r"^url\.", bare=False)
    assert rewrites.splitlines() == [rewrites.splitlines()[0]]
    assert rewrites.startswith(f"url.{new_url}.insteadof ")


def test_a_foreign_path_is_refused(remote: GitRemote, store_for: StoreFor, tmp_path: Path) -> None:
    store = store_for("workspace")
    store.fetch(branches=["main"])
    clone = tmp_path / "mine"
    subprocess.run(["git", "clone", "--quiet", str(remote.path), str(clone)], check=True)

    for call in (
        lambda: store.write_worktree_config(clone, profile=None),
        lambda: store.checkout(clone, "refs/remotes/origin/main"),
    ):
        with pytest.raises(StoreError, match="is not a worktree of repo store") as caught:
            call()
        assert caught.value.category == ErrorCategory.INVALID
    assert "untaped" not in (clone / ".git" / "config").read_text(encoding="utf-8")


def test_an_interrupted_creation_heals(remote: GitRemote, store_for: StoreFor) -> None:
    store = store_for("github")
    store.ensure()
    git(store.path, "config", "--unset", "remote.origin.url")

    store.fetch(branches=["main"])

    assert store.refs() == {"heads/main": remote.oid("main")}


def test_delete_refs_guards_the_symref(store_for: StoreFor) -> None:
    store = store_for("workspace")
    store.fetch(branches=["main"])
    before = all_refs(store.path)

    for name in ("heads/HEAD", "heads/a..b"):
        with pytest.raises(StoreError) as caught:
            store.delete_refs([name])
        assert caught.value.category == ErrorCategory.INVALID
    assert all_refs(store.path) == before


@pytest.mark.parametrize(
    "argv",
    [["fetch", "origin"], ["-c", "x.y=z", "fetch"], ["remote", "update"], ["push"], []],
)
def test_run_refuses_what_reaches_the_remote(store_for: StoreFor, argv: list[str]) -> None:
    store = store_for("github")
    store.ensure()
    with pytest.raises(ValueError, match="local git subcommand"):
        store.run(argv)


def test_run_allows_local_commands(store_for: StoreFor) -> None:
    store = store_for("github")
    store.fetch(branches=["main"])
    assert store.run(["remote", "get-url", "origin"], capture=True).text.strip()
