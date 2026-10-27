"""``dotfiles subscribe``, ``unsubscribe`` and ``repos`` end to end: real git, an isolated home."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from dotfiles_test_support import (
    Upstream,
    as_json,
    git,
    json_rows,
    run,
    subscribe,
)
from untaped.sdk.testing import Home

pytestmark = pytest.mark.usefixtures("dotfiles_env")


def test_subscribe_clones_and_lists_items_with_nothing_enabled(
    make_upstream: Upstream, tmp_path: Path
) -> None:
    bare, _ = make_upstream()
    rows = json_rows(subscribe(bare))
    assert [(r["name"], r["suggested"], r["enabled"]) for r in rows] == [
        ("fish", "manual", False),
        ("starship", "sync", False),
        ("claude", "sync", False),
        ("mac-only", "manual", False),
    ]
    assert (tmp_path / "repos" / "dotfiles" / "dotfiles.yml").is_file()
    repos = as_json(["repos"])
    assert [(r["name"], r["managed"], r["ref"], r["behind"]) for r in repos] == [
        ("dotfiles", True, "main", 0)
    ]
    assert as_json(["items"]) == [r for r in rows if r["name"] != "mac-only"]
    assert [r["excluded"] for r in as_json(["items", "--all"])][-1] == "os is linux, not macos"


def test_subscribe_twice_is_a_conflict_and_name_picks_another(make_upstream: Upstream) -> None:
    bare, _ = make_upstream()
    subscribe(bare)
    again = run(["subscribe", str(bare)])
    assert again.exit_code == 1 and "already subscribed" in again.stderr
    assert subscribe(bare, "--name", "second").exit_code == 0
    assert [r["name"] for r in as_json(["repos"])] == ["dotfiles", "second"]


def test_subscribe_registers_a_checkout_without_cloning(
    make_upstream: Upstream, tmp_path: Path
) -> None:
    _, author = make_upstream()
    subscribe(author, "--name", "local")
    [repo] = as_json(["repos"])
    assert (repo["managed"], repo["target_path"]) == (False, str(author.resolve()))
    assert not (tmp_path / "repos").exists()


def test_subscribe_refuses_a_bad_manifest_and_leaves_no_clone(
    make_upstream: Upstream, tmp_path: Path
) -> None:
    bare, _ = make_upstream(manifest="version: 7\n")
    result = run(["subscribe", str(bare)])
    assert result.exit_code == 1, result.output
    assert "version 7 is not supported" in result.stderr
    assert not (tmp_path / "repos" / "dotfiles").exists()
    assert run(["repos", "--format", "json"]).stdout.strip() in ("", "[]")


def test_subscribe_refuses_an_unknown_column_before_cloning(
    make_upstream: Upstream, tmp_path: Path
) -> None:
    bare, _ = make_upstream()
    result = run(["subscribe", str(bare), "--format", "json", "-c", "name,nope"])
    assert result.exit_code == 2, result.output
    assert "unknown column 'nope'" in result.stderr
    assert not (tmp_path / "repos" / "dotfiles").exists()
    assert run(["repos", "--format", "json"]).stdout.strip() in ("", "[]")


def test_subscribe_with_a_tag_as_ref_is_refused_and_leaves_no_clone(
    make_upstream: Upstream, tmp_path: Path
) -> None:
    bare, author = make_upstream()
    git(author, "tag", "v1")
    git(author, "push", "-q", "origin", "v1")
    result = run(["subscribe", str(bare), "--ref", "v1"])
    assert result.exit_code == 1, result.output
    assert "checked out no branch" in result.stderr
    assert not (tmp_path / "repos" / "dotfiles").exists()
    assert subscribe(bare).exit_code == 0  # nothing is left in the way


def test_subscribe_a_path_that_is_not_a_repo_root(make_upstream: Upstream, tmp_path: Path) -> None:
    _, author = make_upstream()
    result = run(["subscribe", str(author / "fish")])
    assert result.exit_code == 1 and "not the root" in result.stderr
    plain = tmp_path / "plain"
    plain.mkdir()
    assert "not inside a git work tree" in run(["subscribe", str(plain)]).stderr


def test_disable_keeps_files_and_unsubscribe_refuses_until_then(
    make_upstream: Upstream, user_home: Path, tmp_path: Path
) -> None:
    bare, _ = make_upstream()
    subscribe(bare)
    run(["enable", "starship"])
    run(["apply", "--yes"])
    refused = run(["unsubscribe", "dotfiles", "--yes"])
    assert refused.exit_code == 1 and "disable --all --repo dotfiles" in refused.stderr
    assert [r["action"] for r in as_json(["disable", "starship"])] == ["deleted"]
    assert (user_home / ".config/starship.toml").exists()
    dry = json.loads(run(["unsubscribe", "dotfiles", "--dry-run", "--format", "json"]).stdout)
    assert dry["action"] == "planned"
    assert (tmp_path / "repos/dotfiles").is_dir()
    row = json.loads(
        run(["unsubscribe", "dotfiles", "--yes", "--delete-clone", "-f", "json"]).stdout
    )
    assert row["action"] == "deleted"
    assert not (tmp_path / "repos/dotfiles").exists()
    assert run(["unsubscribe", "nope", "--yes"]).exit_code == 1


def test_the_state_is_the_plugin_s_own_file(make_upstream: Upstream, home: Home) -> None:
    bare, _ = make_upstream()
    subscribe(bare)
    run(["enable", "fish"])
    state = yaml.safe_load((home.path / "plugins" / "dotfiles" / "state.yml").read_text())
    assert [repo["name"] for repo in state["repos"]] == ["dotfiles"]
    assert [item["id"] for item in state["items"]] == ["dotfiles/fish"]
    assert not (home.path / "state.yml").exists()
