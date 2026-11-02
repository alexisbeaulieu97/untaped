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


def _store(url: str) -> RepoStore:
    return RepoStore.for_url(url, plugin="recipe", error=PackFetchError)


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


def test_an_unknown_rev_is_not_found(remote: GitRemote, tmp_path: Path) -> None:
    with pytest.raises(PackFetchError, match="no branch, tag or commit 'nope'") as raised:
        fetch_pack_source(remote.url, rev="nope", dest=tmp_path / "pack", plugin="recipe")

    assert raised.value.category is ErrorCategory.NOT_FOUND


def test_an_option_like_rev_is_refused_before_any_git(remote: GitRemote, tmp_path: Path) -> None:
    with pytest.raises(UsageError, match="rev"):
        fetch_pack_source(
            remote.url, rev="--upload-pack=x", dest=tmp_path / "pack", plugin="recipe"
        )

    assert not _store(remote.url).exists()


def test_a_link_in_the_pack_is_refused(remote: GitRemote, tmp_path: Path) -> None:
    os.symlink("/etc/passwd", remote.work / "leak")
    author = ["-c", "user.name=Test", "-c", "user.email=test@example.invalid"]
    for args in (
        ["add", "leak"],
        ["commit", "--quiet", "-m", "link"],
        ["push", "--quiet", "origin", "HEAD:refs/heads/main"],
    ):
        subprocess.run(["git", *author, *args], cwd=remote.work, check=True)

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
