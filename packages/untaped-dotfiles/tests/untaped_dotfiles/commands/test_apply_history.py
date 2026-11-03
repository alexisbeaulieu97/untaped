"""``dotfiles apply`` and what an entry's history says: who moved a target, which entries
were asked for, and what a stuck clone holds."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from dotfiles_test_support import (
    FILES,
    GIT,
    STARSHIP,
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

pytestmark = pytest.mark.usefixtures("dotfiles_env")

DUO = """\
version: 1
description: two files in one item
policy: manual
inputs:
  where: {description: Where}
items:
  both:
    files:
      - {path: a.txt, target: ~/a.txt, mode: copy}
      - {path: b.txt, target: "~/{{ where }}/b.txt", mode: copy}
"""


def _package(source: str, package: str) -> object:
    ran = installed().cli(
        ["config", "get", f"dotfiles.sources.{source}.packages.{package}", "--format", "json"]
    )
    if int(ran.exit) != 0:
        return None
    return json.loads(ran.stdout)["value"]


# -- who moved a target ------------------------------------------------------------


def test_a_target_the_author_moves_waits_under_manual(
    make_remote: MakeRemote, user_home: Path
) -> None:
    remote = make_remote()
    subscribe(remote.url, "--add", "starship")
    as_json(["add", "starship", "--policy", "manual"])
    as_json(["apply", "--yes"])
    moved = STARSHIP.replace("~/.config/starship.toml", "~/.starship.toml")
    publish(remote, {".untaped/dotfiles/starship.yml": moved})
    result = run(["apply", "--yes", "--format", "json"])
    assert result.exit_code == 3, result.output
    assert (user_home / ".config/starship.toml").is_file()
    assert not (user_home / ".starship.toml").exists()
    assert states() == {"~/.config/starship.toml": "orphan", "~/.starship.toml": "new"}


def test_two_entries_sharing_a_path_each_keep_their_own_records(
    make_remote: MakeRemote, user_home: Path
) -> None:
    remote = make_remote(files={**FILES, "claude/skills/bye/SKILL.md": "# bye\n"})
    subscribe(remote.url, "--add", "claude")
    as_json(["add", "claude", "--policy", "manual"])
    as_json(["apply", "--yes"])
    (remote.work / "claude/skills/bye/SKILL.md").unlink()
    publish(remote, {"claude/skills/hi/SKILL.md": "# hi\n"})
    result = run(["apply", "--yes", "--format", "json"])
    assert result.exit_code == 3, result.output
    found = states()
    for base in ("~/.agents/skills/mine", "~/.claude/skills/mine"):
        assert (user_home / base.removeprefix("~/") / "bye/SKILL.md").is_file()
        assert found[f"{base}/bye/SKILL.md"] == "orphan"
        assert found[f"{base}/hi/SKILL.md"] == "new"


def test_files_of_an_item_the_author_removed_are_orphans(
    make_remote: MakeRemote, user_home: Path
) -> None:
    remote = make_remote()
    subscribe(remote.url, "--add", "git")
    as_json(["apply", "--yes", "--var", "git_profile=home", "--var", "git_email=a@example.com"])
    ignore = user_home / ".config/git/ignore"
    assert ignore.is_symlink()
    without = GIT.replace(
        "  ignore:\n    files:\n      - {path: git/ignore, target: ~/.config/git/ignore}\n", ""
    )
    assert without != GIT
    publish(remote, {".untaped/dotfiles/git.yml": without})
    rows = {r["entry"]: r for r in as_json(["apply", "--yes"])}
    assert rows["~/.config/git/ignore"]["action"] == "deleted"
    assert not ignore.exists() and not ignore.is_symlink()


# -- what was asked for -----------------------------------------------------------


def test_an_entry_that_waited_on_an_input_is_placed_once_answered(
    make_remote: MakeRemote, user_home: Path
) -> None:
    remote = make_remote(packages={"duo": DUO}, files={"a.txt": "a\n", "b.txt": "b\n"})
    subscribe(remote.url, "--add", "duo")
    first = run(["apply", "--yes"])
    assert first.exit_code == 3 and "add dotfiles/duo --var where=VALUE" in first.stderr
    as_json(["add", "duo", "--var", "where=x"])  # the hint, as printed
    assert states()["~/x/b.txt"] == "pending"
    as_json(["apply", "--yes"])
    assert (user_home / "x/b.txt").read_text() == "b\n"


def test_an_entry_that_waited_is_placed_however_its_input_is_answered(
    make_remote: MakeRemote, user_home: Path
) -> None:
    remote = make_remote(packages={"duo": DUO}, files={"a.txt": "a\n", "b.txt": "b\n"})
    subscribe(remote.url, "--add", "duo")
    assert run(["apply", "--yes"]).exit_code == 3
    rows = {r["entry"]: r for r in as_json(["apply", "--yes", "--var", "where=y"])}
    assert rows["~/y/b.txt"]["action"] == "created"
    ran = installed().cli(
        ["config", "set", "dotfiles.sources.dotfiles.packages.duo.inputs.where", "z"]
    )
    assert int(ran.exit) == 0, ran.stderr  # by hand: the moved target is asked for too
    as_json(["apply", "--yes"])
    assert (user_home / "z/b.txt").is_file() and not (user_home / "y/b.txt").exists()


def test_answering_an_input_again_does_not_ask_for_entries_the_author_added(
    make_remote: MakeRemote, user_home: Path
) -> None:
    remote = make_remote(packages={"duo": DUO}, files={"a.txt": "a\n", "b.txt": "b\n"})
    subscribe(remote.url, "--add", "duo")
    as_json(["apply", "--yes", "--var", "where=x"])
    added = DUO + '      - {path: c.txt, target: "~/{{ where }}/c.txt", mode: copy}\n'
    publish(remote, {".untaped/dotfiles/duo.yml": added, "c.txt": "c\n"})
    assert run(["apply", "--yes", "--var", "where=x"]).exit_code == 3  # the same value
    assert not (user_home / "x/c.txt").exists()
    as_json(["add", "duo", "--var", "where=w"])
    assert run(["apply", "--yes"]).exit_code == 3
    assert (user_home / "w/b.txt").is_file()  # moved by the input: asked for
    assert not (user_home / "w/c.txt").exists()
    assert states()["~/w/c.txt"] == "new"


def test_an_entry_waiting_on_an_input_counts_as_attention(
    make_remote: MakeRemote, dotfiles_env: Path
) -> None:
    subscribe(make_remote().url, "--add", "git")
    as_json(["add", "git", "--var", "git_email=a@example.com"])
    assert run(["apply", "--yes"]).exit_code == 3
    summary = json.loads((dotfiles_env / "status.json").read_text())
    assert summary["attention"] == 1 and summary["apply"] == 0
    assert run(["status", "--check"]).exit_code == 3


def test_a_var_for_a_package_that_is_off_exits_2_and_keeps_it_off(
    make_remote: MakeRemote,
) -> None:
    subscribe(make_remote().url, "--add", "starship")
    result = run(["apply", "git", "--yes", "--var", "git_profile=home"])
    assert result.exit_code == 2 and "declared only by packages that are off" in result.output
    assert "add dotfiles/git" in result.output
    assert _package("dotfiles", "git") is None
    assert "~/.config/git/ignore" not in states()


def test_a_var_for_a_secret_input_exits_2(make_folder: MakeFolder) -> None:
    secret = GIT.replace(
        "git_email: {description: Email on commits}",
        "git_email: {description: Email on commits}\n  token: {description: A token, secret: true}",
    )
    folder = make_folder("vault", packages={"vault": secret})
    subscribe(str(folder), "--add", "vault")
    result = run(["apply", "--yes", "--var", "token=s3cret"])
    assert result.exit_code == 2 and "is a secret" in result.output


def test_a_value_that_does_not_fit_its_input_exits_2(make_folder: MakeFolder) -> None:
    typed = GIT.replace(
        "git_profile: {description: Which identity file}",
        "git_profile: {description: Which identity file, type: int}",
    )
    subscribe(str(make_folder("typed", packages={"git": typed})), "--add", "git")
    for command in (["apply", "--yes"], ["add", "git"]):
        result = run([*command, "--var", "git_profile=abc"])
        assert result.exit_code == 2 and "cannot coerce value to int" in result.output


def test_an_answer_that_no_longer_fits_fails_only_its_package(
    make_remote: MakeRemote, user_home: Path
) -> None:
    remote = make_remote()
    subscribe(remote.url, "--add", "git", "--add", "starship")
    as_json(["apply", "--yes", "--var", "git_email=a@example.com", "--var", "git_profile=home"])
    typed = GIT.replace(
        "git_profile: {description: Which identity file}",
        "git_profile: {description: Which identity file, type: int}",
    )
    publish(remote, {".untaped/dotfiles/git.yml": typed, "starship.toml": "changed\n"})
    result = run(["apply", "--yes", "--format", "json"])
    assert result.exit_code == 1 and "cannot coerce value to int" in result.stderr
    assert (user_home / ".config/starship.toml").read_text() == "changed\n"
    assert (user_home / ".config/git/home.inc").exists()  # left as it is
    assert run(["status", "--check"]).exit_code == 1
    assert run(["apply", "--yes", "--var", "git_profile=3"]).exit_code == 0  # answered again


# -- a stuck clone ----------------------------------------------------------------


def test_a_link_into_a_clone_with_local_changes_is_not_converted(
    make_remote: MakeRemote, user_home: Path, tmp_path: Path
) -> None:
    remote = make_remote()
    subscribe(remote.url, "--add", "fish")
    as_json(["apply", "--yes"])
    target = user_home / ".config/fish/config.fish"
    target.write_text("edited through the link\n")  # lands in the clone
    as_json(["add", "fish", "--policy", "manual"])
    publish(remote, {"fish/conf.d/abbr.fish": "abbr x y\n"})
    result = run(["apply", "--yes", "--format", "json"])
    assert result.exit_code == 1, result.output
    row = {r["entry"]: r for r in json_rows(result)}["~/.config/fish/config.fish"]
    assert row["action"] == "failed" and "local changes" in str(row["detail"])
    assert target.is_symlink() and target.read_text() == "edited through the link\n"
    clone = tmp_path / "clones" / "dotfiles"
    subprocess.run(["git", "-C", str(clone), "checkout", "--", "."], check=True)
    rows = {r["entry"]: r for r in json_rows(run(["apply", "--yes", "--format", "json"]))}
    assert str(rows["~/.config/fish/config.fish"]["detail"]).startswith("became a copy")
    assert not target.is_symlink() and target.read_text() == FILES["fish/config.fish"]


def test_a_link_that_moved_with_the_clone_keeps_its_diff(make_remote: MakeRemote) -> None:
    remote = make_remote()
    subscribe(remote.url, "--add", "fish")
    as_json(["apply", "--yes"])
    publish(remote, {"fish/config.fish": "upstream\n"})
    rows = {r["entry"]: r for r in as_json(["apply", "--yes"])}
    diff = str(rows["~/.config/fish/config.fish"]["diff"])
    assert "+upstream" in diff


def test_a_link_into_an_edited_clone_that_is_not_moving_waits_too(
    make_remote: MakeRemote, user_home: Path
) -> None:
    subscribe(make_remote().url, "--add", "fish")
    as_json(["apply", "--yes"])
    target = user_home / ".config/fish/config.fish"
    target.write_text("edited through the link\n")
    as_json(["add", "fish", "--policy", "manual"])
    result = run(["apply", "--yes", "--format", "json"])
    assert result.exit_code == 1, result.output
    row = {r["entry"]: r for r in json_rows(result)}["~/.config/fish/config.fish"]
    assert row["action"] == "failed" and "local changes" in str(row["detail"])
    assert target.is_symlink()


def test_a_binary_file_over_an_existing_one_says_so(
    make_remote: MakeRemote, user_home: Path
) -> None:
    package = STARSHIP.replace(
        "      - {path: starship.toml, target: ~/.config/starship.toml, mode: copy}\n",
        "      - {path: starship.toml, target: ~/.config/starship.toml, mode: copy}\n"
        "      - {path: img.bin, target: ~/img.bin, mode: copy}\n",
    )
    remote = make_remote(packages={"starship": package})
    (remote.work / "img.bin").write_bytes(b"\x00\xffbinary")
    publish(remote, {})
    subscribe(remote.url, "--add", "starship")
    (user_home / "img.bin").write_bytes(b"\x00\xfeother")
    rows = {r["entry"]: r for r in as_json(["apply", "--dry-run"])}
    assert "binary, 8 bytes" in str(rows["~/img.bin"]["detail"])
