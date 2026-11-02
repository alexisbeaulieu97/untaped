"""``packs add``, ``sync`` and ``remove`` with a git URL, end to end through the repo store."""

from __future__ import annotations

import json
from pathlib import Path

from recipe_test_support import library_dir, run
from untaped.sdk import home
from untaped_git.api import store_key
from untaped_git.testing import GitRemote, git_remote

_MANIFEST = (
    "[project]\n"
    'name = "untaped-recipe-alpha"\n'
    'version = "0.1.0"\n'
    'requires-python = ">=3.14"\n'
    "dependencies = []\n\n"
    "[tool.untaped_recipe.recipes]\n"
    '"seed" = { path = "recipes/seed.yml" }\n'
)


def _pack_remote(tmp_path: Path, name: str = "acme/alpha") -> GitRemote:
    remote = git_remote(tmp_path, name)
    remote.commit("pyproject.toml", _MANIFEST)
    remote.commit("recipes/seed.yml", "version: 1\nsteps: []\n")
    return remote


def _store_repo(url: str) -> Path:
    return home().expanduser().joinpath("plugins", "git", "store", *store_key(url))


def test_a_git_pack_installs_syncs_and_is_released_on_remove(tmp_path: Path) -> None:
    remote = _pack_remote(tmp_path)

    added = run(["packs", "add", remote.url, "--rev", "main", "--format", "json"])
    assert added.exit_code == 0, added.output
    assert json.loads(added.stdout)["commit"] == remote.oid("main")
    assert (library_dir() / "packs" / "alpha" / "recipes" / "seed.yml").is_file()
    assert _store_repo(remote.url).is_dir()

    moved = remote.commit("recipes/seed.yml", "version: 1\ndescription: new\nsteps: []\n")
    synced = run(["packs", "sync", "alpha", "--yes", "--format", "json"])
    assert synced.exit_code == 0, synced.output
    assert json.loads(synced.stdout)[0]["commit"] == moved

    removed = run(["packs", "remove", "alpha", "--yes"])
    assert removed.exit_code == 0, removed.output
    assert not _store_repo(remote.url).exists()


def test_a_url_another_pack_still_uses_stays_in_the_store(tmp_path: Path) -> None:
    remote = _pack_remote(tmp_path)
    for name in ("alpha", "beta"):
        added = run(["packs", "add", remote.url, "--name", name])
        assert added.exit_code == 0, added.output

    removed = run(["packs", "remove", "alpha", "--yes"])

    assert removed.exit_code == 0, removed.output
    assert _store_repo(remote.url).is_dir()


def test_replacing_a_pack_from_another_url_releases_the_old_one(tmp_path: Path) -> None:
    old = _pack_remote(tmp_path, "acme/old")
    new = _pack_remote(tmp_path, "acme/new")
    assert run(["packs", "add", old.url, "--name", "alpha"]).exit_code == 0

    replaced = run(["packs", "add", new.url, "--name", "alpha", "--force"])

    assert replaced.exit_code == 0, replaced.output
    assert not _store_repo(old.url).exists()
    assert _store_repo(new.url).is_dir()
