"""``dotfiles apply`` end to end: placing, following a source and refusing local edits."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from dotfiles_test_support import (
    FILES,
    MakeFolder,
    MakeRemote,
    answered,
    as_json,
    git,
    installed,
    json_rows,
    publish,
    run,
    states,
    subscribe,
)
from untaped.sdk.testing import ScriptedTerminal

pytestmark = pytest.mark.usefixtures("dotfiles_env")


def test_apply_dry_run_writes_nothing(make_remote: MakeRemote, user_home: Path) -> None:
    subscribe(make_remote().url, "--add", "starship")
    rows = as_json(["apply", "--dry-run"])
    assert [(r["action"], r["state"]) for r in rows] == [("planned", "pending")]
    assert not (user_home / ".config" / "starship.toml").exists()
    assert states() == {"~/.config/starship.toml": "pending"}


def test_apply_places_links_per_child_copies_and_merges(
    make_remote: MakeRemote, user_home: Path, tmp_path: Path
) -> None:
    subscribe(make_remote().url, "--add", "fish", "--add", "starship", "--add", "claude")
    conf = user_home / ".config" / "fish" / "conf.d"
    conf.mkdir(parents=True)
    (conf / "fisher.fish").write_text("# fisher's own file\n")
    (user_home / ".claude").mkdir()
    (user_home / ".claude" / "settings.json").write_text(
        '{"model": "x", "enabledPlugins": {"a": 1}}'
    )
    rows = as_json(["apply", "--yes"])
    assert {r["action"] for r in rows} == {"created"}
    assert not conf.is_symlink() and (conf / "fisher.fish").read_text() == "# fisher's own file\n"
    clone = tmp_path / "clones" / "dotfiles"
    assert os.readlink(conf / "abbr.fish") == str(clone / "fish/conf.d/abbr.fish")
    assert os.readlink(user_home / ".config/fish/config.fish") == str(clone / "fish/config.fish")
    assert (user_home / ".config/starship.toml").read_text().startswith("[character]")
    assert not (user_home / ".config/starship.toml").is_symlink()
    settings = json.loads((user_home / ".claude/settings.json").read_text())
    assert settings == {"model": "x", "enabledPlugins": {"a": 1, "mine": True}, "theme": "dark"}
    assert os.readlink(user_home / ".agents/skills/mine/hello/SKILL.md").endswith("hello/SKILL.md")
    assert os.readlink(user_home / ".claude/skills/mine/hello/SKILL.md").endswith("hello/SKILL.md")
    assert set(states().values()) == {"applied"}
    assert [r["action"] for r in as_json(["apply", "--yes"])] == ["unchanged"] * 7


def test_apply_takes_selectors(make_remote: MakeRemote, user_home: Path) -> None:
    subscribe(make_remote().url, "--add", "fish", "--add", "starship")
    rows = as_json(["apply", "starship", "--yes"])
    assert [r["package"] for r in rows] == ["starship"]
    assert not (user_home / ".config/fish/config.fish").exists()


def test_apply_keeps_a_foreign_file_aside(
    make_remote: MakeRemote, user_home: Path, tmp_path: Path
) -> None:
    subscribe(make_remote().url, "--add", "starship")
    target = user_home / ".config" / "starship.toml"
    target.parent.mkdir(parents=True)
    target.write_text("mine\n")
    assert states() == {"~/.config/starship.toml": "foreign"}
    [row] = as_json(["apply", "--yes"])
    assert row["action"] == "created" and "kept the previous version at" in str(row["detail"])
    [kept] = list((tmp_path / "kept").rglob("starship.toml"))
    assert kept.read_text() == "mine\n"
    assert kept.relative_to(tmp_path / "kept").parts[:3] == ("dotfiles", "starship", "config")


def test_apply_without_a_terminal_needs_yes_and_a_decline_changes_nothing(
    make_remote: MakeRemote, user_home: Path, terminal: ScriptedTerminal
) -> None:
    subscribe(make_remote().url, "--add", "starship")
    result = run(["apply"])
    assert result.exit_code == 2 and "--yes" in result.output
    declined = answered(terminal, False, ["apply"])
    assert declined.exit_code == 1
    assert not (user_home / ".config/starship.toml").exists()


def test_apply_refuses_a_local_edit_unless_forced(make_remote: MakeRemote, user_home: Path) -> None:
    subscribe(make_remote().url, "--add", "starship")
    as_json(["apply", "--yes"])
    target = user_home / ".config/starship.toml"
    target.write_text("edited\n")
    refused = run(["apply", "--yes", "--format", "json"])
    assert refused.exit_code == 1
    assert [r["action"] for r in json_rows(refused)] == ["conflict"]
    assert target.read_text() == "edited\n"
    dry = run(["apply", "--dry-run", "--format", "json"])
    assert dry.exit_code == 1
    [row] = as_json(["apply", "--yes", "--force"])
    assert row["action"] == "updated" and target.read_text().startswith("[character]")


def test_apply_moves_the_clone_and_places_what_the_source_holds_now(
    make_remote: MakeRemote, user_home: Path, tmp_path: Path
) -> None:
    remote = make_remote()
    subscribe(remote.url, "--add", "fish", "--add", "starship")
    as_json(["apply", "--yes"])
    head = publish(
        remote,
        {"fish/config.fish": "set -gx EDITOR hx\n", "starship.toml": "new\n"},
    )
    rows = {r["entry"]: r for r in as_json(["apply", "--yes"])}
    assert rows["~/.config/starship.toml"]["action"] == "updated"
    assert rows["~/.config/fish/config.fish"]["action"] == "updated"
    assert git(tmp_path / "clones" / "dotfiles", "rev-parse", "HEAD") == head
    assert (user_home / ".config/fish/config.fish").read_text() == "set -gx EDITOR hx\n"
    assert (user_home / ".config/starship.toml").read_text() == "new\n"


def test_apply_reads_the_packages_as_just_fetched(make_remote: MakeRemote, user_home: Path) -> None:
    remote = make_remote()
    subscribe(remote.url, "--add", "starship")
    publish(
        remote,
        {
            ".untaped/dotfiles/starship.yml": (
                "version: 1\nitems:\n  config:\n    files:\n"
                "      - {path: starship.toml, target: ~/.starship.toml, mode: copy}\n"
            )
        },
    )
    [row] = as_json(["apply", "--yes"])
    assert row["entry"] == "~/.starship.toml" and (user_home / ".starship.toml").is_file()


def test_apply_takes_back_an_orphaned_child_and_leaves_foreign_files(
    make_remote: MakeRemote, user_home: Path
) -> None:
    remote = make_remote()
    subscribe(remote.url, "--add", "fish")
    as_json(["apply", "--yes"])
    conf = user_home / ".config/fish/conf.d"
    (conf / "mine.fish").write_text("mine\n")
    git(remote.work, "rm", "-q", "fish/conf.d/path.fish")
    publish(remote, {}, "drop path.fish")
    rows = {r["entry"]: r for r in as_json(["apply", "--yes"])}
    assert rows["~/.config/fish/conf.d/path.fish"]["action"] == "deleted"
    assert not (conf / "path.fish").is_symlink()
    assert (conf / "mine.fish").read_text() == "mine\n"


def test_a_folder_source_is_read_as_it_stands(make_folder: MakeFolder, user_home: Path) -> None:
    folder = make_folder("dots")
    subscribe(str(folder), "--add", "fish")
    as_json(["apply", "--yes"])
    assert os.readlink(user_home / ".config/fish/config.fish") == str(
        folder.resolve() / "fish/config.fish"
    )
    (folder / "fish/config.fish").write_text("edited in place\n")
    assert states()["~/.config/fish/config.fish"] == "applied"


def test_apply_reports_a_failed_fetch_and_exits_1(make_remote: MakeRemote, user_home: Path) -> None:
    remote = make_remote()
    subscribe(remote.url, "--add", "starship")
    remote.path.rename(remote.path.with_name("gone.git"))
    result = run(["apply", "--yes"])
    assert result.exit_code == 1, result.output
    assert "dotfiles" in result.stderr
    dry = run(["apply", "--dry-run"])
    assert dry.exit_code == 0, dry.output


def test_a_new_machine_clones_on_apply(
    make_remote: MakeRemote, user_home: Path, tmp_path: Path
) -> None:
    """A ``config.yml`` copied to a new machine names the sources; ``apply`` clones them."""
    remote = make_remote()
    ran = installed().cli(["config", "set", "dotfiles.sources.dotfiles.url", remote.url])
    assert int(ran.exit) == 0, ran.stderr
    ran = installed().cli(
        ["config", "set", "dotfiles.sources.dotfiles.packages.starship.policy", "sync"]
    )
    assert int(ran.exit) == 0, ran.stderr
    result = run(["apply", "--yes", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert "cloned 'dotfiles' at main" in result.stderr
    assert (user_home / ".config/starship.toml").is_file()
    assert (tmp_path / "clones" / "dotfiles" / "starship.toml").is_file()


def test_an_input_with_no_value_leaves_its_entries_pending(
    make_remote: MakeRemote, user_home: Path
) -> None:
    subscribe(make_remote().url, "--add", "git")
    rows = {r["entry"]: r for r in as_json(["status"])}
    waiting = rows["~/.config/git/{{ git_profile }}.inc"]
    assert waiting["state"] == "pending"
    assert "dotfiles.sources.dotfiles.packages.git.inputs.git_profile" in str(waiting["detail"])
    result = run(["apply", "--yes", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert [r["entry"] for r in json_rows(result)] == ["~/.config/git/ignore"]
    assert "git_profile" in result.stderr
    ran = installed().cli(
        ["config", "set", "dotfiles.sources.dotfiles.packages.git.inputs.git_profile", "work"]
    )
    assert int(ran.exit) == 0, ran.stderr
    as_json(["apply", "--yes"])
    assert (user_home / ".config/git/work.inc").is_symlink()


def test_a_default_renders_a_target(make_remote: MakeRemote, user_home: Path) -> None:
    subscribe(make_remote().url, "--add", "claude")
    as_json(["apply", "--yes"])
    assert (user_home / ".claude/settings.json").is_file()


def test_an_excluded_item_is_not_placed(make_remote: MakeRemote, user_home: Path) -> None:
    subscribe(make_remote().url, "--add", "claude")
    ran = installed().cli(["config", "set", "dotfiles.tags", '["claude-plugin-dev"]'])
    assert int(ran.exit) == 0, ran.stderr
    rows = as_json(["apply", "--yes"])
    assert {r["item"] for r in rows} == {"skills"}
    assert {r["state"] for r in as_json(["status", "--excluded"]) if r["item"] == "settings"} == {
        "excluded"
    }


def test_files_unchanged_keep_their_contents(make_remote: MakeRemote, user_home: Path) -> None:
    subscribe(make_remote().url, "--add", "starship")
    as_json(["apply", "--yes"])
    assert (user_home / ".config/starship.toml").read_text() == FILES["starship.toml"]
