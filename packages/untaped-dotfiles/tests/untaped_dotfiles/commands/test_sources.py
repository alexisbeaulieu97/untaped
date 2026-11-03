"""``dotfiles subscribe`` and ``unsubscribe``: sources, their clones, and the packages they list."""

from __future__ import annotations

from pathlib import Path

import pytest

from dotfiles_test_support import (
    FILES,
    PACKAGES,
    MakeFolder,
    MakeRemote,
    as_json,
    git,
    installed,
    json_rows,
    run,
    subscribe,
)
from untaped_dotfiles.sources import classify
from untaped_git.testing import listing, make_repo

pytestmark = pytest.mark.usefixtures("dotfiles_env")


def test_subscribing_a_url_lists_its_packages_and_turns_on_the_added_ones(
    make_remote: MakeRemote, tmp_path: Path
) -> None:
    remote = make_remote()
    rows = json_rows(subscribe(remote.url, "--add", "fish", "--add", "starship"))
    assert [(r["name"], r["on"], r["policy"], r["suggested"]) for r in rows] == [
        ("claude", False, None, "sync"),
        ("fish", True, "manual", "manual"),
        ("git", False, None, "sync"),
        ("mac", False, None, "manual"),
        ("starship", True, "sync", "sync"),
    ]
    assert rows[3]["excluded"] == "macos only"
    clone = tmp_path / "clones" / "dotfiles"
    assert (clone / "fish" / "config.fish").is_file()


def test_the_clone_is_a_detached_worktree_of_the_git_plugins_store(
    make_remote: MakeRemote, tmp_path: Path
) -> None:
    remote = make_remote()
    subscribe(remote.url)
    clone = tmp_path / "clones" / "dotfiles"
    head = git(clone, "rev-parse", "HEAD")
    assert head == remote.oid("main")
    assert run_git_symbolic(clone) == ""  # detached: no branch checked out
    common = Path(git(clone, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    assert tmp_path / "clones" not in common.parents  # the repo lives in the store


def run_git_symbolic(clone: Path) -> str:
    import subprocess

    found = subprocess.run(
        ["git", "symbolic-ref", "-q", "HEAD"], cwd=clone, capture_output=True, text=True
    )
    return found.stdout.strip()


def test_a_folder_is_read_as_it_stands_and_never_cloned(
    make_folder: MakeFolder, tmp_path: Path
) -> None:
    folder = make_folder("dots")
    rows = json_rows(subscribe(str(folder), "--add", "starship"))
    assert {r["name"] for r in rows} == {"claude", "fish", "git", "mac", "starship"}
    assert not (tmp_path / "clones").exists()
    shown = as_json(["show", "starship"])
    assert shown[0]["on"] is True


def test_subscribe_names_the_source_after_the_last_segment(make_remote: MakeRemote) -> None:
    remote = make_remote("My-Dots")
    result = subscribe(remote.url)
    assert {r["source"] for r in json_rows(result)} == {"my-dots"}
    assert "subscribed 'my-dots' at main" in result.stderr


def test_dry_run_lists_the_packages_and_writes_nothing(
    make_remote: MakeRemote, tmp_path: Path
) -> None:
    remote = make_remote()
    result = run(["subscribe", remote.url, "--add", "fish", "--dry-run", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert [r["name"] for r in json_rows(result) if r["on"]] == ["fish"]
    assert not (tmp_path / "clones").exists()
    assert run(["show", "--format", "json"]).stdout.strip() in ("", "[]")


def test_an_invalid_package_is_an_error_row_and_the_source_stays(
    make_remote: MakeRemote,
) -> None:
    remote = make_remote(packages={**PACKAGES, "broken": "version: 1\nitems: {}\n"})
    result = run(["subscribe", remote.url, "--format", "json"])
    assert result.exit_code == 1, result.output
    rows = {r["name"]: r for r in json_rows(result)}
    error = rows["broken"]["error"]
    assert isinstance(error, dict) and "items" in str(error["message"])
    assert rows["fish"]["error"] is None
    shown = run(["show", "--format", "json"])
    assert shown.exit_code == 1
    assert {r["name"] for r in json_rows(shown)} >= {"broken", "fish"}


def test_adding_a_package_the_source_lacks_or_that_doesnt_apply_exits_2(
    make_remote: MakeRemote, tmp_path: Path
) -> None:
    remote = make_remote()
    missing = run(["subscribe", remote.url, "--add", "nope"])
    assert missing.exit_code == 2 and "has no package 'nope'" in missing.output
    excluded = run(["subscribe", remote.url, "--add", "mac"])
    assert excluded.exit_code == 2 and "macos only" in excluded.output
    assert not (tmp_path / "clones" / "dotfiles").exists()  # the clone went again


@pytest.mark.parametrize(
    ("given", "kind"),
    [
        ("/x/dots", "folder"),
        ("~/dots", "folder"),
        ("./dots", "folder"),
        ("C:\\dots", "folder"),
        ("https://git.example/dots.git", "url"),
        ("git@git.example:me/dots.git", "url"),
        ("me/dots", "name"),
        ("dots", "name"),
    ],
)
def test_source_kinds_are_told_by_how_they_start(given: str, kind: str) -> None:
    assert classify(given) == kind


def test_a_repo_name_asks_the_repo_sources_even_when_such_a_folder_exists(
    make_remote: MakeRemote, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote = make_remote("me/dots")
    (tmp_path / "work" / "me" / "dots").mkdir(parents=True)
    monkeypatch.chdir(tmp_path / "work")
    lister = listing(make_repo("me/dots", "git.example"))
    ran = installed({"forge": (lister,)}).cli(
        ["dotfiles", "subscribe", "me/dots", "--format", "json"]
    )
    assert int(ran.exit) == 0, ran.stderr
    assert lister.calls == 1
    clone = tmp_path / "clones" / "dots"
    assert git(clone, "rev-parse", "HEAD") == remote.oid("main")


def test_subscribing_a_name_twice_exits_2(make_remote: MakeRemote) -> None:
    remote = make_remote()
    subscribe(remote.url)
    again = run(["subscribe", remote.url])
    assert again.exit_code == 2 and "already subscribed" in again.output


def test_unsubscribe_takes_files_back_and_releases_the_clone(
    make_remote: MakeRemote, tmp_path: Path, user_home: Path
) -> None:
    remote = make_remote()
    subscribe(remote.url, "--add", "starship", "--add", "fish")
    as_json(["apply", "--yes"])
    assert (user_home / ".config/starship.toml").is_file()
    store = Path(
        git(
            tmp_path / "clones" / "dotfiles",
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        )
    )
    rows = as_json(["unsubscribe", "dotfiles", "--yes"])
    assert {r["action"] for r in rows} == {"deleted"}
    assert not (user_home / ".config/starship.toml").exists()
    assert not (user_home / ".config/fish/config.fish").is_symlink()
    assert not (tmp_path / "clones" / "dotfiles").exists()
    assert not store.exists()
    assert run(["unsubscribe", "dotfiles"]).exit_code != 0


def test_unsubscribe_keep_files_leaves_them(make_remote: MakeRemote, user_home: Path) -> None:
    remote = make_remote()
    subscribe(remote.url, "--add", "starship")
    as_json(["apply", "--yes"])
    rows = as_json(["unsubscribe", "dotfiles", "--keep-files", "--yes"])
    assert [r["detail"] for r in rows] == ["forgotten; the file stays"]
    assert (user_home / ".config/starship.toml").is_file()


def test_unsubscribe_dry_run_previews(make_remote: MakeRemote, user_home: Path) -> None:
    remote = make_remote()
    subscribe(remote.url, "--add", "starship")
    as_json(["apply", "--yes"])
    rows = as_json(["unsubscribe", "dotfiles", "--dry-run"])
    assert [(r["action"], r["detail"]) for r in rows] == [("planned", "deletes the copy")]
    assert (user_home / ".config/starship.toml").is_file()


def test_unsubscribe_without_a_terminal_needs_yes(make_remote: MakeRemote) -> None:
    remote = make_remote()
    subscribe(remote.url)
    result = run(["unsubscribe", "dotfiles"])
    assert result.exit_code == 2 and "--yes" in result.output


def test_a_changed_url_reclones_on_apply(make_remote: MakeRemote, tmp_path: Path) -> None:
    first = make_remote("dotfiles")
    second = make_remote("other", files={**FILES, "starship.toml": "second\n"})
    subscribe(first.url, "--add", "starship")
    result = installed().cli(["config", "set", "dotfiles.sources.dotfiles.url", second.url])
    assert int(result.exit) == 0, result.stderr
    stale = run(["status"])
    assert stale.exit_code == 1 and "changed in config.yml since it was cloned" in stale.output
    as_json(["apply", "--yes"])
    clone = tmp_path / "clones" / "dotfiles"
    assert git(clone, "rev-parse", "HEAD") == second.oid("main")
    assert (Path.home() / ".config/starship.toml").read_text() == "second\n"


def test_subscribing_dot_names_the_source_after_the_folder(
    make_folder: MakeFolder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(make_folder("mydots"))
    result = run(["subscribe", ".", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert {r["source"] for r in json_rows(result)} == {"mydots"}


def test_a_relative_source_path_in_config_is_refused() -> None:
    ran = installed().cli(["config", "set", "dotfiles.sources.dots.path", "dots"])
    assert int(ran.exit) != 0
    assert "path must be absolute" in "\n".join(str(each) for each in ran.stderr)


def test_two_sources_on_one_url_keep_their_own_clones(
    make_remote: MakeRemote, user_home: Path, tmp_path: Path
) -> None:
    remote = make_remote()
    subscribe(remote.url, "--name", "a", "--add", "starship")
    subscribe(remote.url, "--name", "b", "--add", "fish")
    as_json(["apply", "--yes"])
    as_json(["unsubscribe", "a", "--yes"])
    assert not (tmp_path / "clones" / "a").exists()
    assert (tmp_path / "clones" / "b" / "fish" / "config.fish").is_file()
    assert (user_home / ".config/fish/config.fish").read_text() == FILES["fish/config.fish"]


def test_unsubscribe_keep_files_turns_links_into_copies(
    make_remote: MakeRemote, user_home: Path
) -> None:
    subscribe(make_remote().url, "--add", "fish")
    as_json(["apply", "--yes"])
    preview = as_json(["unsubscribe", "dotfiles", "--keep-files", "--dry-run"])
    assert {r["detail"] for r in preview} == {
        "forgets it; the link becomes a copy, since the clone goes"
    }
    as_json(["unsubscribe", "dotfiles", "--keep-files", "--yes"])
    target = user_home / ".config/fish/config.fish"
    assert not target.is_symlink() and target.read_text() == FILES["fish/config.fish"]
