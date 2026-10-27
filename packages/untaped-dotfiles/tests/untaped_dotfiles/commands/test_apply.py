"""``dotfiles apply``, ``sync`` and ``remove`` end to end: placing, following and taking back."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from dotfiles_test_support import (
    MANIFEST,
    Upstream,
    answered,
    as_json,
    bump,
    commit_all,
    git,
    json_rows,
    push,
    run,
    states,
    subscribe,
)
from untaped.sdk.testing import ScriptedTerminal

pytestmark = pytest.mark.usefixtures("dotfiles_env")


def test_apply_dry_run_writes_nothing(make_upstream: Upstream, user_home: Path) -> None:
    bare, _ = make_upstream()
    subscribe(bare)
    run(["enable", "starship"])
    rows = as_json(["apply", "--dry-run"])
    assert [(r["action"], r["state"]) for r in rows] == [("planned", "pending")]
    assert not (user_home / ".config" / "starship.toml").exists()
    assert states() == {"starship.toml": "pending"}


def test_apply_places_links_per_child_copies_and_merges(
    make_upstream: Upstream, user_home: Path, tmp_path: Path
) -> None:
    bare, _ = make_upstream()
    subscribe(bare)
    run(["enable", "fish", "starship", "claude"])
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
    clone = tmp_path / "repos" / "dotfiles"
    assert os.readlink(conf / "abbr.fish") == str(clone / "fish/conf.d/abbr.fish")
    assert os.readlink(user_home / ".config/fish/config.fish") == str(clone / "fish/config.fish")
    assert (user_home / ".config/starship.toml").read_text().startswith("[character]")
    assert not (user_home / ".config/starship.toml").is_symlink()
    settings = json.loads((user_home / ".claude/settings.json").read_text())
    assert settings == {"model": "x", "enabledPlugins": {"a": 1, "mine": True}, "theme": "dark"}
    assert os.readlink(user_home / ".agents/skills/mine/hello/SKILL.md").endswith("hello/SKILL.md")
    assert set(states().values()) == {"applied"}
    assert [r["action"] for r in as_json(["apply", "--yes"])] == ["unchanged"] * 7


def test_apply_keeps_a_foreign_file_aside(
    make_upstream: Upstream, user_home: Path, tmp_path: Path
) -> None:
    bare, _ = make_upstream()
    subscribe(bare)
    run(["enable", "starship"])
    target = user_home / ".config" / "starship.toml"
    target.parent.mkdir(parents=True)
    target.write_text("mine\n")
    assert states() == {"starship.toml": "foreign"}
    [row] = as_json(["apply", "--yes"])
    assert row["action"] == "created" and "kept the previous version at" in str(row["detail"])
    kept = list((tmp_path / "kept").rglob("starship.toml"))
    assert len(kept) == 1 and kept[0].read_text() == "mine\n"
    assert target.read_text().startswith("[character]")


def test_apply_without_a_terminal_needs_yes_and_a_decline_changes_nothing(
    make_upstream: Upstream, user_home: Path, terminal: ScriptedTerminal
) -> None:
    bare, _ = make_upstream()
    subscribe(bare)
    run(["enable", "starship"])
    refused = run(["apply"])
    assert refused.exit_code == 2 and "--yes" in refused.stderr
    declined = answered(terminal, False, ["apply"])
    assert declined.exit_code == 1 and "cancelled; no changes made" in declined.stderr
    header = declined.stderr.splitlines()[0]
    assert header.split() == ["item", "source", "action", "state", "target"]  # the plan
    assert "~/.config/starship.toml" in declined.stderr  # the target, as the person reads it
    assert not (user_home / ".config" / "starship.toml").exists()
    as_json = answered(terminal, False, ["apply", "--format", "json"])
    # Under JSON the preview's lines are info messages, never rows on stdout.
    plan = [json.loads(line)["message"] for line in as_json.stderr.splitlines()][:2]
    assert plan[0].split() == ["item", "source", "action", "state", "target"]
    assert plan[1].split() == [
        "starship",
        "starship.toml",
        "planned",
        "pending",
        "~/.config/starship.toml",
    ]
    assert as_json.stdout == ""
    accepted = answered(terminal, True, ["apply"])
    assert accepted.exit_code == 0, accepted.output
    assert (user_home / ".config" / "starship.toml").exists()


def test_apply_refuses_a_local_edit_unless_forced(
    make_upstream: Upstream, user_home: Path, tmp_path: Path
) -> None:
    bare, _ = make_upstream()
    subscribe(bare)
    run(["enable", "starship"])
    run(["apply", "--yes"])
    target = user_home / ".config" / "starship.toml"
    target.write_text("edited\n")
    assert states() == {"starship.toml": "modified"}
    refused = run(["apply", "--yes", "--format", "json"])
    assert refused.exit_code == 1
    [row] = json_rows(refused)
    assert row["action"] == "conflict" and "--force" in str(row["detail"])
    assert target.read_text() == "edited\n"
    forced = as_json(["apply", "--yes", "--force"])
    assert forced[0]["action"] == "updated"
    assert [p.read_text() for p in (tmp_path / "kept").rglob("starship.toml")] == ["edited\n"]
    assert target.read_text().startswith("[character]")


def test_sync_applies_sync_items_and_holds_a_clone_back_for_manual_links(
    make_upstream: Upstream, user_home: Path, tmp_path: Path, dotfiles_env: Path
) -> None:
    bare, author = make_upstream()
    subscribe(bare)
    run(["enable", "fish", "starship"])  # fish: manual links; starship: sync copy
    run(["apply", "--yes"])
    before = git(tmp_path / "repos" / "dotfiles", "rev-parse", "HEAD")
    bump(
        author, {"starship.toml": "[character]\nsuccess_symbol = '$'\n", "fish/config.fish": "v2\n"}
    )
    result = run(["sync", "--format", "json"])
    assert result.exit_code == 3, result.output
    rows = {str(r["source"]): r for r in json_rows(result)}
    assert rows["starship.toml"]["action"] == "updated"
    assert (user_home / ".config/starship.toml").read_text().endswith("'$'\n")
    assert rows["fish/config.fish"]["action"] == "skipped"
    assert rows["fish/config.fish"]["state"] == "behind"
    assert "apply fish" in str(rows["fish/config.fish"]["detail"])
    assert rows["fish/conf.d/abbr.fish"]["state"] == "applied"
    assert git(tmp_path / "repos" / "dotfiles", "rev-parse", "HEAD") == before
    assert (
        "dotfiles: held back by manual link files: fish/fish/conf.d, fish/fish/config.fish"
        in result.stderr
    )
    assert (user_home / ".config/fish/config.fish").read_text() == "set -gx EDITOR vim\n"
    assert (dotfiles_env / "attention").read_text() == "1\n"
    assert run(["status", "--check"]).exit_code == 3
    # apply fish pulls the clone, and every link on it goes live
    applied = as_json(["apply", "fish", "--yes"])
    assert {str(r["source"]): r["action"] for r in applied}["fish/config.fish"] == "updated"
    assert (user_home / ".config/fish/config.fish").read_text() == "v2\n"
    assert git(tmp_path / "repos" / "dotfiles", "rev-parse", "HEAD") != before
    assert run(["status", "--check"]).exit_code == 0


def test_sync_never_overwrites_a_local_edit(make_upstream: Upstream, user_home: Path) -> None:
    bare, author = make_upstream()
    subscribe(bare)
    run(["enable", "starship"])
    run(["apply", "--yes"])
    (user_home / ".config/starship.toml").write_text("edited\n")
    bump(author, {"starship.toml": "new\n"})
    result = run(["sync", "--format", "json"])
    assert result.exit_code == 3
    [row] = json_rows(result)
    assert (row["action"], row["state"]) == ("skipped", "conflict")
    assert (user_home / ".config/starship.toml").read_text() == "edited\n"


def test_once_places_a_copy_and_skips_updates_silently(
    make_upstream: Upstream, user_home: Path
) -> None:
    bare, author = make_upstream()
    subscribe(bare)
    run(["enable", "fish", "--policy", "once"])
    run(["apply", "--yes"])
    target = user_home / ".config/fish/config.fish"
    assert not target.is_symlink() and target.read_text() == "set -gx EDITOR vim\n"
    bump(author, {"fish/config.fish": "v2\n"})
    result = run(["sync", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert {str(r["source"]): r["action"] for r in json_rows(result)}[
        "fish/config.fish"
    ] == "skipped"
    assert target.read_text() == "set -gx EDITOR vim\n"
    assert states()["fish/config.fish"] == "behind"
    assert "held back" not in result.stderr  # a once link file never holds the clone


def test_sync_removes_an_orphaned_child_and_leaves_foreign_files(
    make_upstream: Upstream, user_home: Path, tmp_path: Path
) -> None:
    bare, author = make_upstream()
    subscribe(bare)
    run(["enable", "fish", "--policy", "sync"])
    run(["apply", "--yes"])
    conf = user_home / ".config/fish/conf.d"
    (conf / "fisher.fish").write_text("theirs\n")
    git(author, "rm", "-q", "fish/conf.d/path.fish")
    commit_all(author, "drop path.fish")
    push(author)
    result = run(["sync", "--format", "json"])
    assert result.exit_code == 0, result.output
    rows = {str(r["source"]): r for r in json_rows(result)}
    assert (rows["fish/conf.d/path.fish"]["action"], rows["fish/conf.d/path.fish"]["state"]) == (
        "deleted",
        "orphan",
    )
    assert sorted(p.name for p in conf.iterdir()) == ["abbr.fish", "fisher.fish"]
    assert "fish/conf.d/path.fish" not in states()


def test_sync_keeps_an_edited_orphan_aside(
    make_upstream: Upstream, user_home: Path, tmp_path: Path
) -> None:
    bare, author = make_upstream()
    subscribe(bare)
    run(["enable", "starship"])  # sync policy, a copy
    run(["apply", "--yes"])
    target = user_home / ".config/starship.toml"
    target.write_text("edited\n")
    git(author, "rm", "-q", "starship.toml")
    commit_all(author, "drop starship.toml")
    push(author)
    result = run(["sync", "--format", "json"])
    assert result.exit_code == 3, result.output  # the manifest still names the gone file
    [row] = json_rows(result)
    assert (row["action"], row["state"]) == ("deleted", "orphan")
    assert "kept the edited version" in str(row["detail"])
    assert not target.exists()
    assert [p.read_text() for p in (tmp_path / "kept").rglob("starship.toml")] == ["edited\n"]


def test_remove_keeps_edited_orphans_and_edited_merged_keys_aside(
    make_upstream: Upstream, user_home: Path, tmp_path: Path
) -> None:
    bare, author = make_upstream()
    subscribe(bare)
    run(["enable", "starship", "--policy", "manual"])  # so sync reports, never removes
    run(["enable", "claude"])
    run(["apply", "--yes"])
    (user_home / ".config/starship.toml").write_text("edited\n")
    settings = user_home / ".claude/settings.json"
    settings.write_text('{"enabledPlugins": {"mine": false}, "theme": "dark", "model": "x"}')
    git(author, "rm", "-q", "starship.toml")
    commit_all(author, "drop starship.toml")
    push(author)
    run(["sync"])  # fetches, so starship.toml is now an orphan
    rows = as_json(["remove", "--all", "--yes"])
    assert {r["action"] for r in rows} == {"deleted"}
    assert [p.read_text() for p in (tmp_path / "kept").rglob("starship.toml")] == ["edited\n"]
    assert json.loads(settings.read_text()) == {"model": "x"}
    kept = [p.read_text() for p in (tmp_path / "kept").rglob("settings.json")]
    assert kept == ['{"enabledPlugins": {"mine": false}, "theme": "dark", "model": "x"}']


def test_an_orphaned_merge_file_keeps_its_format(make_upstream: Upstream, user_home: Path) -> None:
    manifest = (
        "items:\n  claude:\n    policy: sync\n    files:\n"
        "      - {source: claude/settings.json, target: ~/.claude/settings, "
        "mode: merge, format: json}\n"
    )
    bare, author = make_upstream(manifest=manifest)
    subscribe(bare)
    run(["enable", "claude"])
    target = user_home / ".claude/settings"
    target.parent.mkdir()
    target.write_text('{"model": "x"}')
    run(["apply", "--yes"])
    assert json.loads(target.read_text())["theme"] == "dark"
    dropped = "items:\n  claude:\n    files: [{source: mac.txt, target: ~/m}]\n"
    bump(author, {"dotfiles.yml": dropped})
    result = run(["sync", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert target.read_text() == '{\n  "model": "x"\n}\n'  # JSON, not a YAML rendering


def test_sync_dry_run_neither_pulls_nor_writes(
    make_upstream: Upstream, user_home: Path, tmp_path: Path, dotfiles_env: Path
) -> None:
    bare, author = make_upstream()
    subscribe(bare)
    run(["enable", "starship"])
    run(["apply", "--yes"])
    bump(author, {"starship.toml": "new\n"})
    before = git(tmp_path / "repos" / "dotfiles", "rev-parse", "HEAD")
    (dotfiles_env / "attention").unlink()
    result = run(["sync", "--dry-run", "--format", "json"])
    assert result.exit_code == 0, result.output
    [row] = json_rows(result)
    assert (row["action"], row["state"]) == ("planned", "behind")
    assert (user_home / ".config/starship.toml").read_text().startswith("[character]")
    assert git(tmp_path / "repos" / "dotfiles", "rev-parse", "HEAD") == before
    assert not (dotfiles_env / "attention").exists()


def test_a_registered_checkout_is_fetched_but_never_pulled(
    make_upstream: Upstream, user_home: Path, tmp_path: Path
) -> None:
    bare, author = make_upstream()
    mine = tmp_path / "mine"
    git(tmp_path, "clone", "-q", str(bare), str(mine))
    subscribe(mine)
    run(["enable", "fish", "--policy", "sync"])
    run(["apply", "--yes"])
    (mine / "fish/config.fish").write_text("live edit\n")  # the owner's working tree is live
    assert (user_home / ".config/fish/config.fish").read_text() == "live edit\n"
    assert states()["fish/config.fish"] == "applied"
    bump(author, {"fish/config.fish": "v2\n"})
    result = run(["sync", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert git(mine, "rev-list", "--count", "HEAD..origin/main") == "1"
    assert [r["behind"] for r in as_json(["repos"])] == [1]
    assert (mine / "fish/config.fish").read_text() == "live edit\n"


def test_apply_lists_the_manual_link_files_that_move_with_the_clone(
    make_upstream: Upstream, user_home: Path
) -> None:
    bare, author = make_upstream()
    subscribe(bare)
    run(["enable", "fish", "claude", "--skip", "settings"])
    run(["apply", "--yes"])
    bump(author, {"fish/config.fish": "v2\n", "claude/skills/hello/SKILL.md": "# v2\n"})
    plan = as_json(["apply", "claude", "--dry-run"])
    moving = [r for r in plan if r["item"] == "fish"]
    assert [(r["source"], r["action"], r["state"]) for r in moving] == [
        ("fish/config.fish", "updated", "behind")
    ]
    assert "moves with the clone" in str(moving[0]["detail"])
    assert (user_home / ".config/fish/config.fish").read_text() == "set -gx EDITOR vim\n"
    run(["apply", "claude", "--yes"])
    assert (user_home / ".config/fish/config.fish").read_text() == "v2\n"


def test_apply_fetches_the_repos_of_copy_and_merge_files(
    make_upstream: Upstream, user_home: Path
) -> None:
    bare, author = make_upstream()
    subscribe(bare)
    run(["enable", "starship", "claude", "--skip", "claude/skills"])
    run(["apply", "--yes"])
    bump(author, {"starship.toml": "v2\n", "claude/settings.json": '{"theme": "light"}\n'})
    result = run(["apply", "--yes", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert {r["source"]: r["action"] for r in json_rows(result)} == {
        "starship.toml": "updated",
        "claude/settings.json": "updated",
    }
    assert (user_home / ".config/starship.toml").read_text() == "v2\n"
    assert json.loads((user_home / ".claude/settings.json").read_text())["theme"] == "light"


def test_apply_reads_the_manifest_as_just_fetched(make_upstream: Upstream, user_home: Path) -> None:
    bare, author = make_upstream()
    subscribe(bare)
    run(["enable", "starship"])
    run(["apply", "--yes"])
    moved = MANIFEST.replace("~/.config/starship.toml", "~/.starship.toml")
    bump(author, {"dotfiles.yml": moved})
    result = run(["apply", "starship", "--yes", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert (user_home / ".starship.toml").is_file()
    assert not (user_home / ".config/starship.toml").exists()


def test_apply_fetches_a_registered_checkout_without_pulling_it(
    make_upstream: Upstream, tmp_path: Path
) -> None:
    bare, author = make_upstream()
    mine = tmp_path / "mine"
    git(tmp_path, "clone", "-q", str(bare), str(mine))
    subscribe(mine)
    run(["enable", "starship"])
    run(["apply", "--yes"])
    bump(author, {"starship.toml": "v2\n"})
    result = run(["apply", "--yes", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert git(mine, "rev-list", "--count", "HEAD..origin/main") == "1"
    assert [r["behind"] for r in as_json(["repos"])] == [1]


def test_apply_reports_a_failed_fetch_and_exits_1(make_upstream: Upstream, user_home: Path) -> None:
    bare, _ = make_upstream()
    subscribe(bare)
    run(["enable", "starship"])
    run(["apply", "--yes"])
    bare.rename(bare.with_name("gone.git"))
    result = run(["apply", "--yes", "--format", "json"])
    assert result.exit_code == 1, result.output
    assert "fetch failed" in result.stderr
    assert (user_home / ".config/starship.toml").read_text().startswith("[character]")


def test_apply_dry_run_warns_of_a_failed_fetch_and_exits_0(make_upstream: Upstream) -> None:
    bare, _ = make_upstream()
    subscribe(bare)
    run(["enable", "starship"])
    bare.rename(bare.with_name("gone.git"))
    result = run(["apply", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "fetch failed" in result.stderr


def test_a_failed_fetch_is_reported_before_the_question(
    make_upstream: Upstream, terminal: ScriptedTerminal, user_home: Path
) -> None:
    bare, _ = make_upstream()
    subscribe(bare)
    run(["enable", "starship"])
    bare.rename(bare.with_name("gone.git"))
    declined = answered(terminal, False, ["apply"])
    assert declined.exit_code != 0, declined.output
    assert "fetch failed" in declined.stderr
    assert not (user_home / ".config/starship.toml").exists()


def test_apply_dry_run_exits_1_on_a_planned_conflict(
    make_upstream: Upstream, user_home: Path
) -> None:
    bare, _ = make_upstream()
    subscribe(bare)
    run(["enable", "starship"])
    run(["apply", "--yes"])
    (user_home / ".config" / "starship.toml").write_text("edited\n")
    result = run(["apply", "--dry-run", "--format", "json"])
    assert result.exit_code == 1, result.output
    assert [row["action"] for row in json_rows(result)] == ["conflict"]
    assert (user_home / ".config" / "starship.toml").read_text() == "edited\n"


def test_fast_forward_refuses_a_dirty_clone(make_upstream: Upstream, tmp_path: Path) -> None:
    bare, author = make_upstream()
    subscribe(bare)
    run(["enable", "fish", "--policy", "sync"])
    run(["apply", "--yes"])
    (tmp_path / "repos/dotfiles/scratch.txt").write_text("x")
    bump(author, {"fish/config.fish": "v2\n"})
    result = run(["sync", "--format", "json"])
    assert result.exit_code == 1, result.output
    assert "local changes" in result.stderr


def test_apply_reports_a_refused_fast_forward(make_upstream: Upstream, tmp_path: Path) -> None:
    bare, author = make_upstream()
    subscribe(bare)
    run(["enable", "fish"])
    run(["apply", "--yes"])
    (tmp_path / "repos/dotfiles/scratch.txt").write_text("x")
    bump(author, {"fish/config.fish": "v2\n"})
    result = run(["apply", "fish", "--yes", "--format", "json"])
    assert result.exit_code == 1, result.output
    assert "local changes" in result.stderr


def test_remove_deletes_placed_paths_unmerges_and_disables(
    make_upstream: Upstream, user_home: Path, tmp_path: Path, terminal: ScriptedTerminal
) -> None:
    bare, _ = make_upstream()
    subscribe(bare)
    run(["enable", "fish", "starship", "claude"])
    (user_home / ".claude").mkdir()
    (user_home / ".claude/settings.json").write_text('{"model": "x"}')
    run(["apply", "--yes"])
    (user_home / ".config/starship.toml").write_text("edited\n")
    (user_home / ".config/fish/conf.d/fisher.fish").write_text("theirs\n")
    plan = as_json(["remove", "--all", "--dry-run"])
    assert {r["action"] for r in plan} == {"planned"}
    declined = answered(terminal, False, ["remove", "--all"])
    assert declined.exit_code == 1 and "cancelled; no changes made" in declined.stderr
    header = declined.stderr.splitlines()[0]
    assert header.split() == ["item", "source", "action", "state", "target", "detail"]
    rows = as_json(["remove", "--all", "--yes"])
    assert {r["action"] for r in rows} == {"deleted"}
    assert sorted(p.name for p in (user_home / ".config/fish/conf.d").iterdir()) == ["fisher.fish"]
    assert not (user_home / ".config/fish/config.fish").exists()
    assert not (user_home / ".config/starship.toml").exists()
    assert [p.read_text() for p in (tmp_path / "kept").rglob("starship.toml")] == ["edited\n"]
    assert json.loads((user_home / ".claude/settings.json").read_text()) == {"model": "x"}
    assert not (user_home / ".agents/skills/mine/hello/SKILL.md").exists()
    assert as_json(["items"]) and all(not r["enabled"] for r in as_json(["items"]))
    assert run(["status", "--format", "json"]).stdout.strip() in ("", "[]")
