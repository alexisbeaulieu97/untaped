"""Packs from a git URL: fetched through the git plugin's repo store, exported, released."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from untaped.sdk import ErrorCategory, UsageError
from untaped_git.api import RepoStore
from untaped_git.testing import GitRemote, git_remote
from untaped_recipe.errors import PackFetchError
from untaped_recipe.infrastructure import pack_sources
from untaped_recipe.infrastructure.pack_sources import (
    fetch_pack_source,
    is_git_url,
    release_unused_sources,
)

pytestmark = pytest.mark.usefixtures("recipe_run")


@pytest.fixture
def remote(tmp_path: Path) -> GitRemote:
    remote = git_remote(tmp_path, "acme/pack")
    remote.commit("pyproject.toml", "[project]\nname = 'pack'\n")
    return remote


def _store(url: str, plugin: str = "recipe") -> RepoStore:
    return RepoStore.for_url(url, plugin=plugin, error=PackFetchError)


def _commit_by_hand(remote: GitRemote, *paths: str) -> None:
    """Commit ``paths`` of the remote's work tree as they are on disk, and push."""
    author = ["-c", "user.name=Test", "-c", "user.email=test@example.invalid"]
    for args in (
        ["add", *paths],
        ["commit", "--quiet", "-m", "by hand"],
        ["push", "--quiet", "origin", "HEAD:refs/heads/main"],
    ):
        subprocess.run(["git", *author, *args], cwd=remote.work, check=True)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://git.example.com/acme/pack.git", True),
        ("git@git.example.com:acme/pack.git", True),
        ("ssh://git@git.example.com/acme/pack.git", True),
        ("file:///tmp/pack.git", False),
        ("./local-pack", False),
        ("ansible/playbook", False),
    ],
)
def test_is_git_url_matches_supported_cli_prefixes(value: str, expected: bool) -> None:
    assert is_git_url(value) is expected


def test_no_rev_exports_the_default_branch_through_the_store(
    remote: GitRemote, tmp_path: Path
) -> None:
    dest = tmp_path / "pack"

    commit = fetch_pack_source(remote.url, rev=None, dest=dest, plugin="recipe")

    assert commit == remote.oid("main")
    assert (dest / "pyproject.toml").read_text() == "[project]\nname = 'pack'\n"
    assert not (dest / ".git").exists()
    store = _store(remote.url)
    assert store.refs() == {"heads/main": commit}
    assert store.private_file.is_file()


def test_a_branch_rev_follows_the_branch(remote: GitRemote, tmp_path: Path) -> None:
    tip = remote.commit("feature.txt", "x\n", branch="feature")

    commit = fetch_pack_source(remote.url, rev="feature", dest=tmp_path / "a", plugin="recipe")
    moved = remote.commit("feature.txt", "y\n", branch="feature")
    again = fetch_pack_source(remote.url, rev="feature", dest=tmp_path / "b", plugin="recipe")

    assert (commit, again) == (tip, moved)
    assert (tmp_path / "b" / "feature.txt").read_text() == "y\n"
    assert _store(remote.url).refs() == {"heads/feature": moved}


@pytest.mark.parametrize("message", [None, "release 1"])
def test_a_tag_rev_resolves_to_its_commit(
    remote: GitRemote, tmp_path: Path, message: str | None
) -> None:
    tagged = remote.tag("v1", message=message)
    remote.commit("later.txt", "later\n")

    commit = fetch_pack_source(remote.url, rev="v1", dest=tmp_path / "pack", plugin="recipe")

    assert commit == tagged
    assert not (tmp_path / "pack" / "later.txt").exists()


def test_a_commit_id_rev_resolves_from_the_remotes_history(
    remote: GitRemote, tmp_path: Path
) -> None:
    first = remote.oid("main")
    remote.commit("later.txt", "later\n")

    commit = fetch_pack_source(remote.url, rev=first, dest=tmp_path / "pack", plugin="recipe")

    assert commit == first
    assert (tmp_path / "pack" / "pyproject.toml").is_file()
    assert not (tmp_path / "pack" / "later.txt").exists()


def test_a_short_commit_id_resolves(remote: GitRemote, tmp_path: Path) -> None:
    first = remote.oid("main")
    remote.commit("later.txt", "later\n")

    commit = fetch_pack_source(remote.url, rev=first[:10], dest=tmp_path / "pack", plugin="recipe")

    assert commit == first


def test_a_branch_wins_over_a_tag_of_the_same_name(remote: GitRemote, tmp_path: Path) -> None:
    remote.tag("same")
    tip = remote.commit("branch.txt", "b\n", branch="same")

    commit = fetch_pack_source(remote.url, rev="same", dest=tmp_path / "pack", plugin="recipe")

    assert commit == tip


def test_a_commit_only_another_plugin_fetched_is_not_found(
    remote: GitRemote, tmp_path: Path
) -> None:
    gone = remote.commit("gone.txt", "gone\n", branch="gone")
    other = _store(remote.url, plugin="workspace")
    other.fetch(branches=["gone"])
    other.write_private()
    subprocess.run(
        ["git", f"--git-dir={remote.path}", "update-ref", "-d", "refs/heads/gone"], check=True
    )

    with pytest.raises(PackFetchError, match="no branch, tag or commit") as raised:
        fetch_pack_source(remote.url, rev=gone, dest=tmp_path / "pack", plugin="recipe")

    assert raised.value.category is ErrorCategory.NOT_FOUND


def test_a_remote_with_no_default_branch_needs_a_rev(remote: GitRemote, tmp_path: Path) -> None:
    subprocess.run(
        ["git", f"--git-dir={remote.path}", "symbolic-ref", "HEAD", "refs/heads/none"], check=True
    )

    with pytest.raises(PackFetchError, match="no default branch; pass --rev") as raised:
        fetch_pack_source(remote.url, rev=None, dest=tmp_path / "pack", plugin="recipe")

    assert raised.value.category is ErrorCategory.NOT_FOUND


@pytest.mark.parametrize("rev", ["nope", "a..b", "abc"])
def test_an_unknown_rev_is_not_found(remote: GitRemote, tmp_path: Path, rev: str) -> None:
    with pytest.raises(PackFetchError, match=f"no branch, tag or commit '{rev}'") as raised:
        fetch_pack_source(remote.url, rev=rev, dest=tmp_path / "pack", plugin="recipe")

    assert raised.value.category is ErrorCategory.NOT_FOUND


def test_the_tree_is_written_as_committed(remote: GitRemote, tmp_path: Path) -> None:
    (remote.work / ".gitattributes").write_text(
        "ignored.txt export-ignore\nsubst.txt export-subst\n"
    )
    (remote.work / "ignored.txt").write_text("kept\n")
    (remote.work / "subst.txt").write_text("$Format:%H$\n")
    (remote.work / "run.sh").write_text("#!/bin/sh\n")
    (remote.work / "run.sh").chmod(0o755)
    _commit_by_hand(remote, ".gitattributes", "ignored.txt", "subst.txt", "run.sh")
    dest = tmp_path / "pack"

    fetch_pack_source(remote.url, rev=None, dest=dest, plugin="recipe")

    assert (dest / "ignored.txt").read_text() == "kept\n"
    assert (dest / "subst.txt").read_text() == "$Format:%H$\n"
    assert os.access(dest / "run.sh", os.X_OK)
    assert not os.access(dest / "pyproject.toml", os.X_OK)


def test_an_option_like_rev_is_refused_before_any_git(remote: GitRemote, tmp_path: Path) -> None:
    with pytest.raises(UsageError, match="rev"):
        fetch_pack_source(
            remote.url, rev="--upload-pack=x", dest=tmp_path / "pack", plugin="recipe"
        )

    assert not _store(remote.url).exists()


def test_a_link_in_the_pack_is_refused(remote: GitRemote, tmp_path: Path) -> None:
    os.symlink("/etc/passwd", remote.work / "leak")
    _commit_by_hand(remote, "leak")

    with pytest.raises(ValueError, match="pack must not contain symlinks: leak"):
        fetch_pack_source(remote.url, rev=None, dest=tmp_path / "pack", plugin="recipe")


def test_a_url_no_installed_pack_uses_is_released(tmp_path: Path) -> None:
    kept = git_remote(tmp_path, "acme/kept")
    gone = git_remote(tmp_path, "acme/gone")
    for remote in (kept, gone):
        fetch_pack_source(remote.url, rev=None, dest=tmp_path / remote.url[-8:], plugin="recipe")

    release_unused_sources(
        [kept.url, gone.url, "/a/local/pack"], [kept.url, "/a/local/pack"], plugin="recipe"
    )

    assert _store(kept.url).exists()
    assert not _store(gone.url).exists()


def test_a_failed_release_is_a_warning(
    remote: GitRemote, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetch_pack_source(remote.url, rev=None, dest=tmp_path / "pack", plugin="recipe")
    warnings: list[str] = []

    def refuse(self: RepoStore, **_: object) -> None:
        raise PackFetchError("repo is locked")

    monkeypatch.setattr(RepoStore, "release", refuse)
    monkeypatch.setattr(pack_sources._diag, "warning", warnings.append)

    release_unused_sources([remote.url], [], plugin="recipe")

    assert warnings == [f"could not release the repo store's copy of {remote.url}: repo is locked"]


def test_a_commit_id_never_resolves_through_a_ref_named_like_it(
    remote: GitRemote, tmp_path: Path
) -> None:
    first = remote.oid("main")
    later = remote.commit("later.txt", "later\n")
    fetch_pack_source(remote.url, rev=None, dest=tmp_path / "a", plugin="recipe")
    prefix = first[:10]
    subprocess.run(
        [
            "git",
            f"--git-dir={_store(remote.url).path}",
            "update-ref",
            f"refs/heads/{prefix}",
            later,
        ],
        check=True,
    )

    commit = fetch_pack_source(remote.url, rev=prefix.upper(), dest=tmp_path / "b", plugin="recipe")

    assert commit == first


def test_a_tree_entry_named_git_is_refused(remote: GitRemote, tmp_path: Path) -> None:
    def git(*args: str, stdin: str | None = None) -> str:
        return subprocess.run(
            ["git", *args], cwd=remote.work, input=stdin, capture_output=True, text=True, check=True
        ).stdout.strip()

    blob = git("hash-object", "-w", "--stdin", stdin="[core]\n")
    inner = git("mktree", stdin=f"100644 blob {blob}\tconfig\n")
    listing = git("ls-tree", "HEAD")
    root = git("mktree", stdin=f"{listing}\n040000 tree {inner}\t.git\n")
    author = ["-c", "user.name=Test", "-c", "user.email=test@example.invalid"]
    commit = git(*author, "commit-tree", root, "-p", "HEAD", "-m", "planted")
    git("push", "--quiet", "origin", f"{commit}:refs/heads/main")

    with pytest.raises(ValueError, match=r"unsafe path: '\.git/config'"):
        fetch_pack_source(remote.url, rev=None, dest=tmp_path / "pack", plugin="recipe")

    assert not (tmp_path / "pack").exists()


def test_a_pack_that_cannot_be_written_is_a_fetch_error(remote: GitRemote, tmp_path: Path) -> None:
    (tmp_path / "file").write_text("")

    with pytest.raises(PackFetchError, match="could not write the pack") as raised:
        fetch_pack_source(remote.url, rev=None, dest=tmp_path / "file" / "pack", plugin="recipe")

    assert raised.value.category is ErrorCategory.FAILED


def test_an_annotated_tag_object_id_resolves_to_its_commit(
    remote: GitRemote, tmp_path: Path
) -> None:
    tagged = remote.tag("v1", message="release")
    tag_object = subprocess.run(
        ["git", f"--git-dir={remote.path}", "rev-parse", "refs/tags/v1"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    commit = fetch_pack_source(remote.url, rev=tag_object, dest=tmp_path / "pack", plugin="recipe")

    assert tag_object != tagged
    assert commit == tagged


def test_a_short_id_naming_several_commits_asks_for_a_longer_one(
    remote: GitRemote, tmp_path: Path
) -> None:
    author = ["-c", "user.name=Test", "-c", "user.email=test@example.invalid"]
    parent = remote.oid("main")
    tree = f"{parent}^{{tree}}"
    seen: dict[str, str] = {}
    while True:
        commit = subprocess.run(
            ["git", *author, f"--git-dir={remote.path}", "commit-tree", tree, "-p", parent],
            input=f"commit {len(seen)}\n",
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        if commit[:4] in seen:
            break
        seen[commit[:4]] = commit
        parent = commit
    subprocess.run(
        ["git", f"--git-dir={remote.path}", "update-ref", "refs/heads/main", commit], check=True
    )

    with pytest.raises(PackFetchError, match="pass a longer id") as raised:
        fetch_pack_source(remote.url, rev=commit[:4], dest=tmp_path / "a", plugin="recipe")
    resolved = fetch_pack_source(remote.url, rev=commit, dest=tmp_path / "b", plugin="recipe")

    assert raised.value.category is ErrorCategory.CONFLICT
    assert resolved == commit
