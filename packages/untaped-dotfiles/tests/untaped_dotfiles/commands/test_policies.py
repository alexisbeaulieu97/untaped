"""``dotfiles apply`` by policy: the 7.2 table end to end, conversions, inputs and the preview."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dotfiles_test_support import (
    FILES,
    GIT,
    MakeFolder,
    MakeRemote,
    as_json,
    installed,
    json_rows,
    publish,
    run,
    states,
    subscribe,
)
from untaped.sdk.testing import ScriptedTerminal

pytestmark = pytest.mark.usefixtures("dotfiles_env")

_CONFIG = Path(".config/fish/config.fish")


def _inputs(source: str, package: str) -> dict[str, object]:
    ran = installed().cli(
        ["config", "get", f"dotfiles.sources.{source}.packages.{package}", "--format", "json"]
    )
    assert int(ran.exit) == 0, ran.stderr
    found: dict[str, dict[str, object]] = json.loads(ran.stdout)
    inputs = found["value"].get("inputs") or {}
    assert isinstance(inputs, dict)
    return inputs


# -- links only for sync, and conversions ---------------------------------------


def test_a_link_becomes_a_copy_of_what_it_shows_before_the_clone_moves(
    make_remote: MakeRemote, user_home: Path
) -> None:
    remote = make_remote()
    subscribe(remote.url, "--add", "fish")
    as_json(["apply", "--yes"])
    target = user_home / _CONFIG
    assert target.is_symlink()
    as_json(["add", "fish", "--policy", "manual"])
    publish(remote, {"fish/config.fish": "upstream\n"})
    result = run(["apply", "--yes", "--format", "json"])
    assert result.exit_code == 3, result.output  # the new version waits for a person
    rows = {r["entry"]: r for r in json_rows(result)}
    row = rows["~/.config/fish/config.fish"]
    assert row["action"] == "updated" and str(row["detail"]).startswith("became a copy")
    assert not target.is_symlink() and target.read_text() == FILES["fish/config.fish"]
    assert states()["~/.config/fish/config.fish"] == "behind"
    as_json(["apply", "fish", "--yes"])  # named: placed whatever the policy
    assert not target.is_symlink() and target.read_text() == "upstream\n"


def test_a_copy_becomes_a_link_when_its_item_follows_sync(
    make_remote: MakeRemote, user_home: Path
) -> None:
    subscribe(make_remote().url, "--add", "fish")
    as_json(["add", "fish", "--policy", "once"])
    preview = {r["entry"]: r for r in as_json(["apply", "--dry-run"])}
    assert "once places a copy" in str(preview["~/.config/fish/config.fish"]["detail"])
    rows = {r["entry"]: r for r in as_json(["apply", "--yes"])}
    assert rows["~/.config/fish/config.fish"]["mode"] == "copy"
    target = user_home / _CONFIG
    assert target.is_file() and not target.is_symlink()
    as_json(["add", "fish", "--policy", "sync"])
    assert states()["~/.config/fish/config.fish"] == "behind"
    as_json(["apply", "--yes"])
    assert target.is_symlink()


# -- the table, end to end -------------------------------------------------------


def test_new_entries_wait_under_manual_and_apply_under_sync(
    make_remote: MakeRemote, user_home: Path
) -> None:
    remote = make_remote()
    subscribe(remote.url, "--add", "fish")
    as_json(["add", "fish", "--policy", "manual"])
    as_json(["apply", "--yes"])
    publish(remote, {"fish/conf.d/new.fish": "abbr n new\n"})
    as_json(["apply", "--dry-run"])  # fetch
    assert states()["~/.config/fish/conf.d/new.fish"] == "new"
    waiting = run(["apply", "--yes", "--format", "json"])
    assert waiting.exit_code == 3
    assert not (user_home / ".config/fish/conf.d/new.fish").exists()
    as_json(["add", "fish", "--policy", "sync"])
    as_json(["apply", "--yes"])
    assert (user_home / ".config/fish/conf.d/new.fish").is_symlink()


def test_turning_a_package_on_again_counts_as_asking(
    make_remote: MakeRemote, user_home: Path
) -> None:
    subscribe(make_remote().url, "--add", "fish")
    as_json(["add", "fish", "--policy", "manual"])
    as_json(["apply", "--yes"])
    as_json(["remove", "fish", "--yes"])
    as_json(["add", "fish", "--policy", "manual"])
    assert set(states().values()) == {"pending"}
    assert run(["apply", "--yes"]).exit_code == 0
    assert (user_home / _CONFIG).is_file()


def test_once_leaves_a_new_version_alone(make_remote: MakeRemote, user_home: Path) -> None:
    remote = make_remote()
    subscribe(remote.url, "--add", "starship")
    as_json(["add", "starship", "--policy", "once"])
    as_json(["apply", "--yes"])
    publish(remote, {"starship.toml": "changed\n"})
    result = run(["apply", "--yes", "--format", "json"])
    assert result.exit_code == 0, result.output
    [row] = json_rows(result)
    assert row["action"] == "skipped" and row["state"] == "behind"
    assert (user_home / ".config/starship.toml").read_text() == FILES["starship.toml"]
    assert run(["status", "--check"]).exit_code == 0  # nothing for apply or a person


def test_a_package_turned_off_by_hand_is_off_until_removed(
    make_remote: MakeRemote, user_home: Path
) -> None:
    subscribe(make_remote().url, "--add", "starship")
    as_json(["apply", "--yes"])
    ran = installed().cli(["config", "unset", "dotfiles.sources.dotfiles.packages.starship"])
    assert int(ran.exit) == 0, ran.stderr
    [row] = as_json(["status"])
    assert row["state"] == "off" and "remove dotfiles/starship" in str(row["detail"])
    assert run(["status", "--check"]).exit_code == 3
    assert run(["apply", "--yes"]).exit_code == 3
    assert (user_home / ".config/starship.toml").is_file()
    as_json(["remove", "dotfiles/starship", "--yes"])
    assert not (user_home / ".config/starship.toml").exists()
    assert as_json(["status"]) == []


def test_apply_all_places_every_package_whatever_its_policy(
    make_remote: MakeRemote, user_home: Path
) -> None:
    remote = make_remote()
    subscribe(remote.url, "--add", "starship")
    as_json(["add", "starship", "--policy", "manual"])
    as_json(["apply", "--yes"])
    publish(remote, {"starship.toml": "changed\n"})
    assert run(["apply", "--yes"]).exit_code == 3
    assert run(["apply", "--all", "--yes"]).exit_code == 0
    assert (user_home / ".config/starship.toml").read_text() == "changed\n"
    assert run(["apply", "--all", "starship"]).exit_code == 2


# -- inputs ----------------------------------------------------------------------


def _two_gits(make_remote: MakeRemote, make_folder: MakeFolder) -> None:
    subscribe(make_remote().url, "--add", "git")
    work = make_folder("work", packages={"git": GIT.replace("~/.config/git", "~/.work/git")})
    subscribe(str(work), "--add", "git")


def test_one_var_answers_every_package_in_the_run_that_declares_it(
    make_remote: MakeRemote, make_folder: MakeFolder, user_home: Path
) -> None:
    _two_gits(make_remote, make_folder)
    as_json(["apply", "--yes", "--var", "git_profile=home"])
    assert _inputs("dotfiles", "git") == {"git_profile": "home"}
    assert _inputs("work", "git") == {"git_profile": "home"}
    assert (user_home / ".config/git/home.inc").exists()
    assert (user_home / ".work/git/home.inc").exists()
    as_json(["apply", "work/git", "--yes", "--var", "git_profile=acme"])
    assert _inputs("dotfiles", "git") == {"git_profile": "home"}
    assert _inputs("work", "git") == {"git_profile": "acme"}
    unknown = run(["apply", "--yes", "--var", "nobody=x"])
    assert unknown.exit_code == 2 and "nobody" in unknown.stderr


def test_add_answers_inputs_by_the_same_rule(
    make_remote: MakeRemote, make_folder: MakeFolder
) -> None:
    _two_gits(make_remote, make_folder)
    as_json(["add", "dotfiles/git", "--var", "git_email=alex@example.com"])
    assert _inputs("dotfiles", "git") == {"git_email": "alex@example.com"}
    assert _inputs("work", "git") == {}
    assert run(["add", "dotfiles/git", "--var", "nobody=x"]).exit_code == 2
    secret = GIT.replace(
        "git_email: {description: Email on commits}",
        "git_email: {description: Email on commits}\n  token: {description: A token, secret: true}",
    )
    folder = make_folder("vault", packages={"vault": secret})
    subscribe(str(folder), "--add", "vault")
    refused = run(["add", "vault", "--var", "token=x"])
    assert refused.exit_code == 2 and "secret" in refused.stderr


def test_var_wins_over_vars_file(make_remote: MakeRemote, user_home: Path, tmp_path: Path) -> None:
    subscribe(make_remote().url, "--add", "git")
    answers = tmp_path / "answers.yml"
    answers.write_text("git_profile: file\ngit_email: me@example.com\n")
    as_json(["apply", "--yes", "--vars-file", str(answers), "--var", "git_profile=flag"])
    assert _inputs("dotfiles", "git") == {"git_profile": "flag", "git_email": "me@example.com"}


def test_an_input_is_asked_once_and_kept_only_after_the_confirmation(
    make_remote: MakeRemote, user_home: Path, terminal: ScriptedTerminal
) -> None:
    subscribe(make_remote().url, "--add", "git")
    terminal.types("me@example.com", "work").answers(False)
    try:
        declined = run(["apply"])
    finally:
        terminal.absent()
    assert declined.exit_code == 1
    assert _inputs("dotfiles", "git") == {}
    terminal.types("me@example.com", "work").answers(True)
    try:
        done = run(["apply"])
    finally:
        terminal.absent()
    assert done.exit_code == 0, done.output
    assert _inputs("dotfiles", "git") == {"git_profile": "work", "git_email": "me@example.com"}
    assert (user_home / ".config/git/work.inc").is_symlink()
    assert run(["apply", "--yes"]).exit_code == 0  # kept: nothing is asked again


def test_a_changed_input_moves_a_target_whatever_the_policy(
    make_remote: MakeRemote, user_home: Path
) -> None:
    subscribe(make_remote().url, "--add", "git")
    as_json(["add", "git", "--policy", "manual", "--var", "git_profile=work"])
    as_json(["apply", "--yes"])
    assert (user_home / ".config/git/work.inc").is_file()
    result = run(["apply", "--yes", "--var", "git_profile=home", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert (user_home / ".config/git/home.inc").is_file()
    assert not (user_home / ".config/git/work.inc").exists()


# -- the preview -----------------------------------------------------------------


def test_the_preview_shows_each_destination_and_a_diff_over_existing_content(
    make_remote: MakeRemote, user_home: Path
) -> None:
    subscribe(make_remote().url, "--add", "fish", "--add", "starship", "--add", "claude")
    (user_home / ".config").mkdir()
    (user_home / ".config/starship.toml").write_text("mine\n")
    (user_home / ".claude").mkdir()
    (user_home / ".claude/settings.json").write_text('{"model": "opus"}\n')
    (user_home / ".config/fish").mkdir()
    (user_home / _CONFIG).write_text("my fish\n")
    preview = run(["apply", "--dry-run"])
    assert preview.exit_code == 0, preview.output
    text = preview.stderr
    assert "-mine" in text and "+[character]" in text  # a copy over a file
    assert '-  "model": "opus"' in text or '-{"model": "opus"}' in text  # a merge
    assert "-my fish" in text  # a link over a file
    assert "new, 1 line" in text and "+abbr g git" not in text  # a new file: no diff in text
    rows = {r["entry"]: r for r in as_json(["apply", "--dry-run"])}
    assert "+abbr g git" in str(rows["~/.config/fish/conf.d/abbr.fish"]["diff"])
    assert "yours is kept aside first" in str(rows["~/.config/starship.toml"]["detail"])
    assert "merges 2 keys into the existing file" in str(rows["~/.claude/settings.json"]["detail"])


def _snapshot(home: Path) -> dict[str, bytes]:
    """``config.yml`` and every file of the dotfiles plugin's home (state, status files)."""
    root = home / ".untaped"
    files = [root / "config.yml", *(root / "plugins" / "dotfiles").rglob("*")]
    return {str(path): path.read_bytes() for path in files if path.is_file()}


def test_a_dry_run_fetches_but_writes_nothing(make_remote: MakeRemote, user_home: Path) -> None:
    remote = make_remote()
    subscribe(remote.url, "--add", "starship", "--add", "git")
    as_json(["apply", "--yes", "--var", "git_profile=home"])
    before = _snapshot(user_home)
    publish(remote, {"starship.toml": "changed\n"})
    result = run(["apply", "--dry-run", "--var", "git_profile=work"])
    assert result.exit_code == 0, result.output
    assert _snapshot(user_home) == before
    assert (user_home / ".config/starship.toml").read_text() == FILES["starship.toml"]
    assert not (user_home / ".config/git/work.inc").exists()
    assert states()["~/.config/starship.toml"] == "behind"  # it fetched
