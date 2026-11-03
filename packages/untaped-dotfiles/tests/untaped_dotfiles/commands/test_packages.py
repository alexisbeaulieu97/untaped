"""``dotfiles add``, ``remove`` and ``show``, and the selectors every command takes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dotfiles_test_support import (
    MakeFolder,
    MakeRemote,
    as_json,
    json_rows,
    run,
    subscribe,
)

pytestmark = pytest.mark.usefixtures("dotfiles_env")


def _on(rows: list[dict[str, object]]) -> dict[str, object]:
    return {str(r["name"]): r["on"] for r in rows}


def test_add_turns_a_package_on_with_its_authors_policy(make_remote: MakeRemote) -> None:
    subscribe(make_remote().url)
    result = run(["add", "starship", "--format", "json"])
    assert result.exit_code == 0, result.output
    [row] = json_rows(result)
    assert (row["on"], row["policy"], row["chosen"]) == (True, "sync", False)
    assert "turned on dotfiles/starship" in result.stderr
    assert "untaped dotfiles apply" in result.stderr


def test_add_policy_is_this_machines_choice(make_remote: MakeRemote) -> None:
    subscribe(make_remote().url, "--add", "fish")
    [row] = as_json(["add", "fish", "--policy", "sync"])
    assert (row["policy"], row["suggested"], row["chosen"]) == ("sync", "manual", True)
    [shown] = as_json(["show", "fish"])
    assert shown["policy"] == "sync" and shown["chosen"] is True


def test_add_an_item_turns_its_package_on_with_the_other_items_off(
    make_remote: MakeRemote,
) -> None:
    subscribe(make_remote().url)
    result = run(["add", "claude/skills", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert "with its other items off" in result.stderr
    [shown] = as_json(["show", "claude"])
    assert {i["name"]: i["enabled"] for i in shown["items"]} == {  # type: ignore[union-attr]
        "settings": False,
        "skills": True,
    }
    as_json(["add", "claude/settings"])
    [shown] = as_json(["show", "claude"])
    assert all(i["enabled"] for i in shown["items"])  # type: ignore[union-attr]


def test_add_a_package_that_doesnt_apply_exits_2(make_remote: MakeRemote) -> None:
    subscribe(make_remote().url)
    result = run(["add", "mac"])
    assert result.exit_code == 2 and "macos only" in result.output


def test_add_dry_run_writes_nothing(make_remote: MakeRemote) -> None:
    subscribe(make_remote().url)
    result = run(["add", "starship", "--dry-run", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert _on(as_json(["show"]))["starship"] is False


def test_remove_takes_back_and_turns_off(make_remote: MakeRemote, user_home: Path) -> None:
    subscribe(make_remote().url, "--add", "starship", "--add", "claude")
    (user_home / ".claude").mkdir()
    (user_home / ".claude/settings.json").write_text('{"model": "x"}')
    as_json(["apply", "--yes"])
    rows = as_json(["remove", "starship", "claude/settings", "--yes"])
    assert {(r["package"], r["action"]) for r in rows} == {
        ("starship", "deleted"),
        ("claude", "deleted"),
    }
    assert not (user_home / ".config/starship.toml").exists()
    assert json.loads((user_home / ".claude/settings.json").read_text()) == {"model": "x"}
    on = _on(as_json(["show"]))
    assert on["starship"] is False and on["claude"] is True
    assert (user_home / ".claude/skills/mine/hello/SKILL.md").is_symlink()


def test_remove_keeps_an_edited_copy_aside(
    make_remote: MakeRemote, user_home: Path, tmp_path: Path
) -> None:
    subscribe(make_remote().url, "--add", "starship")
    as_json(["apply", "--yes"])
    (user_home / ".config/starship.toml").write_text("edited\n")
    preview = as_json(["remove", "starship", "--dry-run"])
    assert [r["detail"] for r in preview] == ["keeps the edited file aside"]
    [row] = as_json(["remove", "starship", "--yes"])
    assert "kept the edited version at" in str(row["detail"])
    assert [p.read_text() for p in (tmp_path / "kept").rglob("starship.toml")] == ["edited\n"]


def test_remove_keep_files_turns_off_without_touching_files(
    make_remote: MakeRemote, user_home: Path
) -> None:
    subscribe(make_remote().url, "--add", "starship")
    as_json(["apply", "--yes"])
    as_json(["remove", "starship", "--keep-files", "--yes"])
    assert (user_home / ".config/starship.toml").is_file()
    assert _on(as_json(["show"]))["starship"] is False


def test_show_never_shows_a_secret_and_says_where_values_come_from(
    make_folder: MakeFolder,
) -> None:
    folder = make_folder(
        "dots",
        packages={
            "vault": (
                "version: 1\ninputs:\n  token: {secret: true}\n  region: {default: eu}\n"
                "items:\n  login:\n    steps:\n      - name: login\n"
                "        check: {program: vault}\n        run: [vault, login]\n"
                "        stdin: token\n"
            )
        },
        files={},
    )
    subscribe(str(folder), "--add", "vault")
    [row] = as_json(["show", "vault"])
    inputs = {i["name"]: i for i in row["inputs"]}  # type: ignore[union-attr]
    assert (inputs["token"]["value"], inputs["token"]["origin"]) == (None, None)
    assert (inputs["region"]["value"], inputs["region"]["origin"]) == ("eu", "default")
    [item] = row["items"]  # type: ignore[misc]
    [step] = item["steps"]
    assert (step["policy"], step["chosen"], step["stdin"]) == ("manual", False, "token")


def test_a_bare_name_in_two_sources_is_ambiguous(
    make_remote: MakeRemote, make_folder: MakeFolder
) -> None:
    subscribe(make_remote().url)
    subscribe(str(make_folder("work")))
    result = run(["add", "fish"])
    assert result.exit_code == 2
    assert "package 'fish' is in 2 sources: dotfiles/fish, work/fish" in result.output
    [row] = as_json(["add", "work/fish"])
    assert (row["source"], row["on"]) == ("work", True)


def test_two_parts_read_as_source_package_or_package_item(
    make_remote: MakeRemote, make_folder: MakeFolder
) -> None:
    subscribe(make_remote().url)
    [as_package] = as_json(["show", "dotfiles/fish"])
    assert as_package["source"] == "dotfiles" and len(as_package["items"]) == 1  # type: ignore[arg-type]
    [as_item] = as_json(["show", "claude/skills"])
    assert [i["name"] for i in as_item["items"]] == ["skills"]  # type: ignore[union-attr]
    # a source named like a package with an item named like the source's package: both exist
    skills = "version: 1\nitems:\n  x:\n    files:\n      - {path: a, target: ~/a}\n"
    subscribe(str(make_folder("claude", packages={"skills": skills})))
    result = run(["show", "claude/skills"])
    assert result.exit_code == 2
    assert "names package claude/skills and item dotfiles/claude/skills" in result.output


@pytest.mark.parametrize("selector", ["", "a/b/c/d", "a//b", "/a"])
def test_a_malformed_selector_exits_2(make_remote: MakeRemote, selector: str) -> None:
    subscribe(make_remote().url)
    result = run(["show", selector])
    assert result.exit_code == 2


def test_an_unknown_name_exits_2(make_remote: MakeRemote) -> None:
    subscribe(make_remote().url)
    assert run(["show", "nope"]).exit_code == 2
    assert "has no item 'nope'" in run(["show", "fish/nope"]).output
    assert run(["show", "dotfiles/fish/nope"]).exit_code == 2
