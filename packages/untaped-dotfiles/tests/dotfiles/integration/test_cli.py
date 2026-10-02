"""The dotfiles CLI end to end: real git, isolated home, config and state."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from dotfiles.conftest import commit_all, git, push, write_files
from untaped.testing import CliInvoker, CliResult, ScriptedPromptBackend, invoke_root
from untaped_dotfiles.cli import app

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("dotfiles_env")]
run = CliInvoker().invoke

Upstream = Callable[..., tuple[Path, Path]]


def _rows(result: CliResult) -> list[dict[str, object]]:
    assert result.stdout.strip(), result.output
    rows: list[dict[str, object]] = json.loads(result.stdout)
    return rows


def _json(args: list[str]) -> list[dict[str, object]]:
    result = run(app, [*args, "--format", "json"])
    assert result.exit_code in (0, 3), result.output
    return _rows(result)


def _states(args: list[str] | None = None) -> dict[str, str]:
    rows = _json(["status", *(args or [])])
    return {str(row["source"]): str(row["state"]) for row in rows}


def _subscribe(bare: Path, *extra: str) -> CliResult:
    result = run(app, ["subscribe", str(bare), *extra, "--format", "json"])
    assert result.exit_code == 0, result.output
    return result


def _bump(author: Path, files: dict[str, str], message: str = "update") -> None:
    write_files(author, files)
    commit_all(author, message)
    push(author)


# -- subscribe, items, enable -------------------------------------------------------


def test_subscribe_clones_and_lists_items_with_nothing_enabled(
    make_upstream: Upstream, tmp_path: Path
) -> None:
    bare, _ = make_upstream()
    rows = _rows(_subscribe(bare))
    assert [(r["name"], r["suggested"], r["enabled"]) for r in rows] == [
        ("fish", "manual", False),
        ("starship", "sync", False),
        ("claude", "sync", False),
        ("mac-only", "manual", False),
    ]
    assert (tmp_path / "repos" / "dotfiles" / "dotfiles.yml").is_file()
    repos = _json(["repos"])
    assert [(r["name"], r["managed"], r["ref"], r["behind"]) for r in repos] == [
        ("dotfiles", True, "main", 0)
    ]
    assert _json(["items"]) == [r for r in rows if r["name"] != "mac-only"]
    assert [r["excluded"] for r in _json(["items", "--all"])][-1] == "os is linux, not macos"


def test_subscribe_twice_is_a_conflict_and_name_picks_another(make_upstream: Upstream) -> None:
    bare, _ = make_upstream()
    _subscribe(bare)
    again = run(app, ["subscribe", str(bare)])
    assert again.exit_code == 1 and "already subscribed" in again.stderr
    assert _subscribe(bare, "--name", "second").exit_code == 0
    assert [r["name"] for r in _json(["repos"])] == ["dotfiles", "second"]


def test_subscribe_registers_a_checkout_without_cloning(
    make_upstream: Upstream, tmp_path: Path
) -> None:
    _, author = make_upstream()
    _subscribe(author, "--name", "local")
    [repo] = _json(["repos"])
    assert (repo["managed"], repo["target_path"]) == (False, str(author.resolve()))
    assert not (tmp_path / "repos").exists()


def test_subscribe_refuses_a_bad_manifest_and_leaves_no_clone(
    make_upstream: Upstream, tmp_path: Path
) -> None:
    bare, _ = make_upstream(manifest="version: 7\n")
    result = run(app, ["subscribe", str(bare)])
    assert result.exit_code == 1, result.output
    assert "version 7 is not supported" in result.stderr
    assert not (tmp_path / "repos" / "dotfiles").exists()
    assert run(app, ["repos", "--format", "json"]).stdout.strip() in ("", "[]")


def test_subscribe_with_a_tag_as_ref_is_refused_and_leaves_no_clone(
    make_upstream: Upstream, tmp_path: Path
) -> None:
    bare, author = make_upstream()
    git(author, "tag", "v1")
    git(author, "push", "-q", "origin", "v1")
    result = run(app, ["subscribe", str(bare), "--ref", "v1"])
    assert result.exit_code == 1, result.output
    assert "checked out no branch" in result.stderr
    assert not (tmp_path / "repos" / "dotfiles").exists()
    assert _subscribe(bare).exit_code == 0  # nothing is left in the way


def test_subscribe_a_path_that_is_not_a_repo_root(make_upstream: Upstream, tmp_path: Path) -> None:
    _, author = make_upstream()
    result = run(app, ["subscribe", str(author / "fish")])
    assert result.exit_code == 1 and "not the root" in result.stderr
    plain = tmp_path / "plain"
    plain.mkdir()
    assert "not inside a git work tree" in run(app, ["subscribe", str(plain)]).stderr


def test_enable_records_the_policy_and_skips(make_upstream: Upstream) -> None:
    bare, _ = make_upstream()
    _subscribe(bare)
    rows = _json(["enable", "fish", "claude", "--skip", "settings"])
    assert [(r["name"], r["action"], r["policy"]) for r in rows] == [
        ("fish", "created", "manual"),
        ("claude", "created", "sync"),
    ]
    again = _json(["enable", "fish", "--policy", "sync"])
    assert [(r["name"], r["action"], r["policy"]) for r in again] == [("fish", "updated", "sync")]
    items = {r["name"]: r for r in _json(["items"])}
    assert (items["fish"]["policy"], items["claude"]["skip"]) == ("sync", ["settings"])
    assert run(app, ["enable", "fish", "--skip", "nope"]).exit_code == 2
    assert run(app, ["enable"]).exit_code == 2
    assert run(app, ["enable", "fish", "--all"]).exit_code == 2
    assert run(app, ["enable", "nope"]).exit_code == 1


def test_enable_all_takes_every_item_that_applies_here(make_upstream: Upstream) -> None:
    bare, _ = make_upstream()
    _subscribe(bare)
    rows = _json(["enable", "--all"])
    assert [r["name"] for r in rows] == ["fish", "starship", "claude"]


def test_an_ambiguous_bare_name_is_refused_with_a_hint(make_upstream: Upstream) -> None:
    bare, _ = make_upstream()
    other, _ = make_upstream("work")
    _subscribe(bare)
    _subscribe(other)
    result = run(app, ["enable", "fish"])
    assert result.exit_code == 2, result.output
    assert "several repos" in result.stderr and "--repo" in result.stderr
    assert run(app, ["enable", "fish", "--repo", "work"]).exit_code == 0
    assert run(app, ["status", "fish"]).exit_code == 0  # one enabled: not ambiguous
    run(app, ["enable", "fish", "--repo", "dotfiles"])
    assert run(app, ["status", "fish"]).exit_code == 2
    assert run(app, ["disable", "fish"]).exit_code == 2
    assert [r["repo"] for r in _json(["disable", "fish", "--repo", "work"])] == ["work"]


# -- status and apply ----------------------------------------------------------------


def test_status_before_apply_is_pending_and_writes_the_status_files(
    make_upstream: Upstream, dotfiles_env: Path
) -> None:
    bare, _ = make_upstream()
    _subscribe(bare)
    run(app, ["enable", "fish", "starship"])
    assert _states() == {
        "fish/config.fish": "pending",
        "fish/conf.d/abbr.fish": "pending",
        "fish/conf.d/path.fish": "pending",
        "starship.toml": "pending",
    }
    assert json.loads((dotfiles_env / "status.json").read_text())["pending"] == 4
    assert (dotfiles_env / "attention").read_text() == "0\n"
    assert run(app, ["status", "--check"]).exit_code == 0
    summary = run(app, ["status", "--summary", "--format", "json"])
    assert json.loads(summary.stdout)["total"] == 4


def test_apply_dry_run_writes_nothing(make_upstream: Upstream, home: Path) -> None:
    bare, _ = make_upstream()
    _subscribe(bare)
    run(app, ["enable", "starship"])
    rows = _json(["apply", "--dry-run"])
    assert [(r["action"], r["state"]) for r in rows] == [("planned", "pending")]
    assert not (home / ".config" / "starship.toml").exists()
    assert _states() == {"starship.toml": "pending"}


def test_apply_places_links_per_child_copies_and_merges(
    make_upstream: Upstream, home: Path, tmp_path: Path
) -> None:
    bare, _ = make_upstream()
    _subscribe(bare)
    run(app, ["enable", "fish", "starship", "claude"])
    conf = home / ".config" / "fish" / "conf.d"
    conf.mkdir(parents=True)
    (conf / "fisher.fish").write_text("# fisher's own file\n")
    (home / ".claude").mkdir()
    (home / ".claude" / "settings.json").write_text('{"model": "x", "enabledPlugins": {"a": 1}}')
    rows = _json(["apply", "--yes"])
    assert {r["action"] for r in rows} == {"created"}
    assert not conf.is_symlink() and (conf / "fisher.fish").read_text() == "# fisher's own file\n"
    clone = tmp_path / "repos" / "dotfiles"
    assert os.readlink(conf / "abbr.fish") == str(clone / "fish/conf.d/abbr.fish")
    assert os.readlink(home / ".config/fish/config.fish") == str(clone / "fish/config.fish")
    assert (home / ".config/starship.toml").read_text().startswith("[character]")
    assert not (home / ".config/starship.toml").is_symlink()
    settings = json.loads((home / ".claude/settings.json").read_text())
    assert settings == {"model": "x", "enabledPlugins": {"a": 1, "mine": True}, "theme": "dark"}
    assert os.readlink(home / ".agents/skills/mine/hello/SKILL.md").endswith("hello/SKILL.md")
    assert set(_states().values()) == {"applied"}
    assert [r["action"] for r in _json(["apply", "--yes"])] == ["unchanged"] * 7


def test_apply_keeps_a_foreign_file_aside(
    make_upstream: Upstream, home: Path, tmp_path: Path
) -> None:
    bare, _ = make_upstream()
    _subscribe(bare)
    run(app, ["enable", "starship"])
    target = home / ".config" / "starship.toml"
    target.parent.mkdir(parents=True)
    target.write_text("mine\n")
    assert _states() == {"starship.toml": "foreign"}
    [row] = _json(["apply", "--yes"])
    assert row["action"] == "created" and "kept the previous version at" in str(row["detail"])
    kept = list((tmp_path / "kept").rglob("starship.toml"))
    assert len(kept) == 1 and kept[0].read_text() == "mine\n"
    assert target.read_text().startswith("[character]")


def test_apply_without_a_terminal_needs_yes_and_a_decline_changes_nothing(
    make_upstream: Upstream, home: Path
) -> None:
    bare, _ = make_upstream()
    _subscribe(bare)
    run(app, ["enable", "starship"])
    refused = run(app, ["apply"])
    assert refused.exit_code == 2 and "--yes" in refused.stderr
    declined = run(
        app, ["apply"], terminal=True, prompt_backend=ScriptedPromptBackend(confirms=[False])
    )
    assert declined.exit_code == 1 and "cancelled; no changes made" in declined.stderr
    assert "starship.toml" in declined.stderr  # the plan was previewed
    assert not (home / ".config" / "starship.toml").exists()
    accepted = run(
        app, ["apply"], terminal=True, prompt_backend=ScriptedPromptBackend(confirms=[True])
    )
    assert accepted.exit_code == 0, accepted.output
    assert (home / ".config" / "starship.toml").exists()


def test_apply_refuses_a_local_edit_unless_forced(
    make_upstream: Upstream, home: Path, tmp_path: Path
) -> None:
    bare, _ = make_upstream()
    _subscribe(bare)
    run(app, ["enable", "starship"])
    run(app, ["apply", "--yes"])
    target = home / ".config" / "starship.toml"
    target.write_text("edited\n")
    assert _states() == {"starship.toml": "modified"}
    refused = run(app, ["apply", "--yes", "--format", "json"])
    assert refused.exit_code == 1
    [row] = _rows(refused)
    assert row["action"] == "conflict" and "--force" in str(row["detail"])
    assert target.read_text() == "edited\n"
    forced = _json(["apply", "--yes", "--force"])
    assert forced[0]["action"] == "updated"
    assert [p.read_text() for p in (tmp_path / "kept").rglob("starship.toml")] == ["edited\n"]
    assert target.read_text().startswith("[character]")


# -- sync ---------------------------------------------------------------------------


def test_sync_applies_sync_items_and_holds_a_clone_back_for_manual_links(
    make_upstream: Upstream, home: Path, tmp_path: Path, dotfiles_env: Path
) -> None:
    bare, author = make_upstream()
    _subscribe(bare)
    run(app, ["enable", "fish", "starship"])  # fish: manual links; starship: sync copy
    run(app, ["apply", "--yes"])
    before = git(tmp_path / "repos" / "dotfiles", "rev-parse", "HEAD")
    _bump(
        author, {"starship.toml": "[character]\nsuccess_symbol = '$'\n", "fish/config.fish": "v2\n"}
    )
    result = run(app, ["sync", "--format", "json"])
    assert result.exit_code == 3, result.output
    rows = {str(r["source"]): r for r in _rows(result)}
    assert rows["starship.toml"]["action"] == "updated"
    assert (home / ".config/starship.toml").read_text().endswith("'$'\n")
    assert rows["fish/config.fish"]["action"] == "skipped"
    assert rows["fish/config.fish"]["state"] == "behind"
    assert "apply fish" in str(rows["fish/config.fish"]["detail"])
    assert rows["fish/conf.d/abbr.fish"]["state"] == "applied"
    assert git(tmp_path / "repos" / "dotfiles", "rev-parse", "HEAD") == before
    assert (
        "held back by manual link files: fish/fish/conf.d, fish/fish/config.fish" in result.stderr
    )
    assert (home / ".config/fish/config.fish").read_text() == "set -gx EDITOR vim\n"
    assert (dotfiles_env / "attention").read_text() == "1\n"
    assert run(app, ["status", "--check"]).exit_code == 3
    # apply fish pulls the clone, and every link on it goes live
    applied = _json(["apply", "fish", "--yes"])
    assert {str(r["source"]): r["action"] for r in applied}["fish/config.fish"] == "updated"
    assert (home / ".config/fish/config.fish").read_text() == "v2\n"
    assert git(tmp_path / "repos" / "dotfiles", "rev-parse", "HEAD") != before
    assert run(app, ["status", "--check"]).exit_code == 0


def test_sync_never_overwrites_a_local_edit(make_upstream: Upstream, home: Path) -> None:
    bare, author = make_upstream()
    _subscribe(bare)
    run(app, ["enable", "starship"])
    run(app, ["apply", "--yes"])
    (home / ".config/starship.toml").write_text("edited\n")
    _bump(author, {"starship.toml": "new\n"})
    result = run(app, ["sync", "--format", "json"])
    assert result.exit_code == 3
    [row] = _rows(result)
    assert (row["action"], row["state"]) == ("skipped", "conflict")
    assert (home / ".config/starship.toml").read_text() == "edited\n"


def test_once_places_a_copy_and_skips_updates_silently(make_upstream: Upstream, home: Path) -> None:
    bare, author = make_upstream()
    _subscribe(bare)
    run(app, ["enable", "fish", "--policy", "once"])
    run(app, ["apply", "--yes"])
    target = home / ".config/fish/config.fish"
    assert not target.is_symlink() and target.read_text() == "set -gx EDITOR vim\n"
    _bump(author, {"fish/config.fish": "v2\n"})
    result = run(app, ["sync", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert {str(r["source"]): r["action"] for r in _rows(result)}["fish/config.fish"] == "skipped"
    assert target.read_text() == "set -gx EDITOR vim\n"
    assert _states()["fish/config.fish"] == "behind"
    assert "held back" not in result.stderr  # a once link file never holds the clone


def test_sync_removes_an_orphaned_child_and_leaves_foreign_files(
    make_upstream: Upstream, home: Path, tmp_path: Path
) -> None:
    bare, author = make_upstream()
    _subscribe(bare)
    run(app, ["enable", "fish", "--policy", "sync"])
    run(app, ["apply", "--yes"])
    conf = home / ".config/fish/conf.d"
    (conf / "fisher.fish").write_text("theirs\n")
    git(author, "rm", "-q", "fish/conf.d/path.fish")
    commit_all(author, "drop path.fish")
    push(author)
    result = run(app, ["sync", "--format", "json"])
    assert result.exit_code == 0, result.output
    rows = {str(r["source"]): r for r in _rows(result)}
    assert (rows["fish/conf.d/path.fish"]["action"], rows["fish/conf.d/path.fish"]["state"]) == (
        "deleted",
        "orphan",
    )
    assert sorted(p.name for p in conf.iterdir()) == ["abbr.fish", "fisher.fish"]
    assert "fish/conf.d/path.fish" not in _states()


def test_sync_keeps_an_edited_orphan_aside(
    make_upstream: Upstream, home: Path, tmp_path: Path
) -> None:
    bare, author = make_upstream()
    _subscribe(bare)
    run(app, ["enable", "starship"])  # sync policy, a copy
    run(app, ["apply", "--yes"])
    target = home / ".config/starship.toml"
    target.write_text("edited\n")
    git(author, "rm", "-q", "starship.toml")
    commit_all(author, "drop starship.toml")
    push(author)
    result = run(app, ["sync", "--format", "json"])
    assert result.exit_code == 3, result.output  # the manifest still names the gone file
    [row] = _rows(result)
    assert (row["action"], row["state"]) == ("deleted", "orphan")
    assert "kept the edited version" in str(row["detail"])
    assert not target.exists()
    assert [p.read_text() for p in (tmp_path / "kept").rglob("starship.toml")] == ["edited\n"]


def test_remove_keeps_edited_orphans_and_edited_merged_keys_aside(
    make_upstream: Upstream, home: Path, tmp_path: Path
) -> None:
    bare, author = make_upstream()
    _subscribe(bare)
    run(app, ["enable", "starship", "--policy", "manual"])  # so sync reports, never removes
    run(app, ["enable", "claude"])
    run(app, ["apply", "--yes"])
    (home / ".config/starship.toml").write_text("edited\n")
    settings = home / ".claude/settings.json"
    settings.write_text('{"enabledPlugins": {"mine": false}, "theme": "dark", "model": "x"}')
    git(author, "rm", "-q", "starship.toml")
    commit_all(author, "drop starship.toml")
    push(author)
    run(app, ["sync"])  # fetches, so starship.toml is now an orphan
    rows = _json(["remove", "--all", "--yes"])
    assert {r["action"] for r in rows} == {"deleted"}
    assert [p.read_text() for p in (tmp_path / "kept").rglob("starship.toml")] == ["edited\n"]
    assert json.loads(settings.read_text()) == {"model": "x"}
    kept = [p.read_text() for p in (tmp_path / "kept").rglob("settings.json")]
    assert kept == ['{"enabledPlugins": {"mine": false}, "theme": "dark", "model": "x"}']


def test_an_orphaned_merge_file_keeps_its_format(make_upstream: Upstream, home: Path) -> None:
    manifest = (
        "items:\n  claude:\n    policy: sync\n    files:\n"
        "      - {source: claude/settings.json, target: ~/.claude/settings, "
        "mode: merge, format: json}\n"
    )
    bare, author = make_upstream(manifest=manifest)
    _subscribe(bare)
    run(app, ["enable", "claude"])
    target = home / ".claude/settings"
    target.parent.mkdir()
    target.write_text('{"model": "x"}')
    run(app, ["apply", "--yes"])
    assert json.loads(target.read_text())["theme"] == "dark"
    dropped = "items:\n  claude:\n    files: [{source: mac.txt, target: ~/m}]\n"
    _bump(author, {"dotfiles.yml": dropped})
    result = run(app, ["sync", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert target.read_text() == '{\n  "model": "x"\n}\n'  # JSON, not a YAML rendering


def test_sync_dry_run_neither_pulls_nor_writes(
    make_upstream: Upstream, home: Path, tmp_path: Path, dotfiles_env: Path
) -> None:
    bare, author = make_upstream()
    _subscribe(bare)
    run(app, ["enable", "starship"])
    run(app, ["apply", "--yes"])
    _bump(author, {"starship.toml": "new\n"})
    before = git(tmp_path / "repos" / "dotfiles", "rev-parse", "HEAD")
    (dotfiles_env / "attention").unlink()
    result = run(app, ["sync", "--dry-run", "--format", "json"])
    assert result.exit_code == 0, result.output
    [row] = _rows(result)
    assert (row["action"], row["state"]) == ("planned", "behind")
    assert (home / ".config/starship.toml").read_text().startswith("[character]")
    assert git(tmp_path / "repos" / "dotfiles", "rev-parse", "HEAD") == before
    assert not (dotfiles_env / "attention").exists()


def test_a_registered_checkout_is_fetched_but_never_pulled(
    make_upstream: Upstream, home: Path, tmp_path: Path
) -> None:
    bare, author = make_upstream()
    mine = tmp_path / "mine"
    git(tmp_path, "clone", "-q", str(bare), str(mine))
    _subscribe(mine)
    run(app, ["enable", "fish", "--policy", "sync"])
    run(app, ["apply", "--yes"])
    (mine / "fish/config.fish").write_text("live edit\n")  # the owner's working tree is live
    assert (home / ".config/fish/config.fish").read_text() == "live edit\n"
    assert _states()["fish/config.fish"] == "applied"
    _bump(author, {"fish/config.fish": "v2\n"})
    result = run(app, ["sync", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert git(mine, "rev-list", "--count", "HEAD..origin/main") == "1"
    assert [r["behind"] for r in _json(["repos"])] == [1]
    assert (mine / "fish/config.fish").read_text() == "live edit\n"


def test_apply_lists_the_manual_link_files_that_move_with_the_clone(
    make_upstream: Upstream, home: Path
) -> None:
    bare, author = make_upstream()
    _subscribe(bare)
    run(app, ["enable", "fish", "claude", "--skip", "settings"])
    run(app, ["apply", "--yes"])
    _bump(author, {"fish/config.fish": "v2\n", "claude/skills/hello/SKILL.md": "# v2\n"})
    plan = _json(["apply", "claude", "--dry-run"])
    moving = [r for r in plan if r["item"] == "fish"]
    assert [(r["source"], r["action"], r["state"]) for r in moving] == [
        ("fish/config.fish", "updated", "behind")
    ]
    assert "moves with the clone" in str(moving[0]["detail"])
    assert (home / ".config/fish/config.fish").read_text() == "set -gx EDITOR vim\n"
    run(app, ["apply", "claude", "--yes"])
    assert (home / ".config/fish/config.fish").read_text() == "v2\n"


def test_fast_forward_refuses_a_dirty_clone(make_upstream: Upstream, tmp_path: Path) -> None:
    bare, author = make_upstream()
    _subscribe(bare)
    run(app, ["enable", "fish", "--policy", "sync"])
    run(app, ["apply", "--yes"])
    (tmp_path / "repos/dotfiles/scratch.txt").write_text("x")
    _bump(author, {"fish/config.fish": "v2\n"})
    result = run(app, ["sync", "--format", "json"])
    assert result.exit_code == 1, result.output
    assert "local changes" in result.stderr


# -- diff, remove, disable, unsubscribe -------------------------------------------------


def test_diff_shows_copy_and_merge_changes(make_upstream: Upstream, home: Path) -> None:
    bare, author = make_upstream()
    _subscribe(bare)
    run(app, ["enable", "starship", "claude", "--policy", "manual"])
    run(app, ["apply", "--yes"])
    assert run(app, ["diff"]).stdout == ""
    _bump(author, {"starship.toml": "new\n", "claude/settings.json": '{"theme": "light"}\n'})
    run(app, ["sync"])
    out = run(app, ["diff"]).stdout
    assert "--- a/~/.config/starship.toml" in out and "+new" in out
    assert '+  "theme": "light"' in out and '-  "theme": "dark"' in out
    assert "SKILL.md" not in out


def test_remove_deletes_placed_paths_unmerges_and_disables(
    make_upstream: Upstream, home: Path, tmp_path: Path
) -> None:
    bare, _ = make_upstream()
    _subscribe(bare)
    run(app, ["enable", "fish", "starship", "claude"])
    (home / ".claude").mkdir()
    (home / ".claude/settings.json").write_text('{"model": "x"}')
    run(app, ["apply", "--yes"])
    (home / ".config/starship.toml").write_text("edited\n")
    (home / ".config/fish/conf.d/fisher.fish").write_text("theirs\n")
    plan = _json(["remove", "--all", "--dry-run"])
    assert {r["action"] for r in plan} == {"planned"}
    rows = _json(["remove", "--all", "--yes"])
    assert {r["action"] for r in rows} == {"deleted"}
    assert sorted(p.name for p in (home / ".config/fish/conf.d").iterdir()) == ["fisher.fish"]
    assert not (home / ".config/fish/config.fish").exists()
    assert not (home / ".config/starship.toml").exists()
    assert [p.read_text() for p in (tmp_path / "kept").rglob("starship.toml")] == ["edited\n"]
    assert json.loads((home / ".claude/settings.json").read_text()) == {"model": "x"}
    assert not (home / ".agents/skills/mine/hello/SKILL.md").exists()
    assert _json(["items"]) and all(not r["enabled"] for r in _json(["items"]))
    assert run(app, ["status", "--format", "json"]).stdout.strip() in ("", "[]")


def test_disable_keeps_files_and_unsubscribe_refuses_until_then(
    make_upstream: Upstream, home: Path, tmp_path: Path
) -> None:
    bare, _ = make_upstream()
    _subscribe(bare)
    run(app, ["enable", "starship"])
    run(app, ["apply", "--yes"])
    refused = run(app, ["unsubscribe", "dotfiles", "--yes"])
    assert refused.exit_code == 1 and "disable --all --repo dotfiles" in refused.stderr
    assert [r["action"] for r in _json(["disable", "starship"])] == ["deleted"]
    assert (home / ".config/starship.toml").exists()
    dry = _json(["unsubscribe", "dotfiles", "--dry-run"])
    assert dry[0]["action"] == "planned"
    assert (tmp_path / "repos/dotfiles").is_dir()
    [row] = _json(["unsubscribe", "dotfiles", "--yes", "--delete-clone"])
    assert row["action"] == "deleted"
    assert not (tmp_path / "repos/dotfiles").exists()
    assert run(app, ["unsubscribe", "nope", "--yes"]).exit_code == 1


def test_an_item_gone_from_the_manifest_is_reported_not_fatal(
    make_upstream: Upstream, home: Path
) -> None:
    bare, author = make_upstream()
    _subscribe(bare)
    run(app, ["enable", "starship", "fish"])
    run(app, ["apply", "--yes"])
    only_config = "files: [{source: fish/config.fish, target: ~/.config/fish/config.fish}]"
    _bump(author, {"dotfiles.yml": f"items:\n  fish:\n    {only_config}\n"})
    result = run(app, ["sync", "--format", "json"])
    assert result.exit_code == 1, result.output
    assert "'starship' is no longer in the manifest" in result.stderr
    assert "disable starship" in result.stderr
    rows = {str(r["source"]): r for r in _rows(result)}
    assert set(rows) == {"fish/config.fish", "fish/conf.d/abbr.fish", "fish/conf.d/path.fish"}
    assert rows["fish/conf.d/abbr.fish"]["state"] == "orphan"


def test_status_of_an_unknown_enabled_item_hints_enable(make_upstream: Upstream) -> None:
    bare, _ = make_upstream()
    _subscribe(bare)
    result = run(app, ["status", "fish"])
    assert result.exit_code == 1
    assert "enabled item not found: 'fish'" in result.stderr
    assert "hint: run `untaped dotfiles enable fish`" in result.stderr


def test_the_root_mounts_the_capability(make_upstream: Upstream) -> None:
    listed = invoke_root(["capabilities", "--format", "json"])
    assert ("dotfiles", "ready") in [(r["name"], r["status"]) for r in json.loads(listed.stdout)]
    assert invoke_root(["dotfiles", "repos"]).exit_code == 0
