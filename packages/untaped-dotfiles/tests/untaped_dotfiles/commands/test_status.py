"""``dotfiles status`` end to end: the state of each entry, read offline, and the status files."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dotfiles_test_support import (
    MakeFolder,
    MakeRemote,
    as_json,
    json_rows,
    publish,
    run,
    states,
    subscribe,
)

pytestmark = pytest.mark.usefixtures("dotfiles_env")


def test_status_before_apply_is_pending_and_writes_the_status_files(
    make_remote: MakeRemote, dotfiles_env: Path
) -> None:
    subscribe(make_remote().url, "--add", "fish", "--add", "starship")
    assert states() == {
        "~/.config/fish/config.fish": "pending",
        "~/.config/fish/conf.d/abbr.fish": "pending",
        "~/.config/fish/conf.d/path.fish": "pending",
        "~/.config/starship.toml": "pending",
    }
    assert json.loads((dotfiles_env / "status.json").read_text())["pending"] == 4
    assert (dotfiles_env / "attention").read_text() == "0\n"
    assert json.loads((dotfiles_env / "status.json").read_text())["apply"] == 4
    assert run(["status", "--check"]).exit_code == 3  # something for apply to do
    as_json(["apply", "--yes"])
    assert run(["status", "--check"]).exit_code == 0


def test_status_takes_selectors_and_then_leaves_the_status_files(
    make_remote: MakeRemote, dotfiles_env: Path
) -> None:
    subscribe(make_remote().url, "--add", "fish", "--add", "starship")
    assert set(states(["starship"])) == {"~/.config/starship.toml"}
    assert not (dotfiles_env / "status.json").exists()


def test_a_source_change_is_behind_until_apply_and_check_exits_3(
    make_remote: MakeRemote, dotfiles_env: Path
) -> None:
    remote = make_remote()
    subscribe(remote.url, "--add", "fish", "--add", "starship")
    as_json(["apply", "--yes"])
    publish(remote, {"fish/config.fish": "changed\n", "starship.toml": "changed\n"})
    assert set(states().values()) == {"applied"}  # status fetches nothing
    as_json(["apply", "--dry-run"])  # a preview fetches, and moves nothing
    found = states()
    assert found["~/.config/starship.toml"] == "behind"
    assert found["~/.config/fish/config.fish"] == "behind"
    assert run(["status", "--check"]).exit_code == 3
    assert json.loads((dotfiles_env / "status.json").read_text())["apply"] == 2  # both sync
    assert (dotfiles_env / "attention").read_text() == "0\n"
    as_json(["add", "starship", "--policy", "manual"])
    assert (dotfiles_env / "attention").read_text() == "1\n"  # manual waits for a person


def test_a_local_edit_is_modified(make_remote: MakeRemote, user_home: Path) -> None:
    subscribe(make_remote().url, "--add", "starship")
    as_json(["apply", "--yes"])
    (user_home / ".config/starship.toml").write_text("edited\n")
    assert states() == {"~/.config/starship.toml": "modified"}


def test_a_removed_target_is_missing(make_remote: MakeRemote, user_home: Path) -> None:
    subscribe(make_remote().url, "--add", "starship")
    as_json(["apply", "--yes"])
    (user_home / ".config/starship.toml").unlink()
    assert states() == {"~/.config/starship.toml": "missing"}


def _unreadable(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    """Make ``path`` unreadable, root or not: permission bits do not stop root."""
    read_bytes = Path.read_bytes

    def refuse(self: Path) -> bytes:
        if self == path:
            raise PermissionError(13, "Permission denied", str(self))
        return read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", refuse)


def test_an_unreadable_target_exits_1_and_counts_in_attention(
    make_remote: MakeRemote, user_home: Path, dotfiles_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    subscribe(make_remote().url, "--add", "starship")
    as_json(["apply", "--yes"])
    _unreadable(monkeypatch, user_home / ".config/starship.toml")
    result = run(["status", "--format", "json"])
    assert result.exit_code == 1, result.output
    [row] = json_rows(result)
    assert row["state"] == "error" and row["error"] is not None
    assert (dotfiles_env / "attention").read_text() == "1\n"


def test_a_package_file_that_breaks_is_reported_not_fatal(make_remote: MakeRemote) -> None:
    remote = make_remote()
    subscribe(remote.url, "--add", "fish", "--add", "starship")
    publish(remote, {".untaped/dotfiles/fish.yml": "version: 1\nitems: {}\n"})
    as_json(["apply", "--yes", "starship"])
    result = run(["status", "--format", "json"])
    assert result.exit_code == 1
    assert "dotfiles/fish" in result.stderr
    assert {r["package"] for r in json_rows(result)} == {"starship"}


def test_a_newer_package_format_asks_to_upgrade(make_remote: MakeRemote) -> None:
    remote = make_remote(packages={"next": "version: 2\nitems: {}\n"})
    result = run(["subscribe", remote.url, "--format", "json"])
    assert result.exit_code == 1
    [row] = json_rows(result)
    assert "upgrade untaped" in str(row["error"])


def test_a_target_at_or_above_home_is_refused_once_rendered(make_folder: MakeFolder) -> None:
    folder = make_folder(
        "dots",
        packages={
            "home": (
                "version: 1\ninputs:\n  dir: {default: '~'}\n"
                "items:\n  x:\n    files:\n      - {path: a, target: '{{ dir }}'}\n"
            )
        },
        files={"a": "a\n"},
    )
    subscribe(str(folder), "--add", "home")
    result = run(["status", "--format", "json"])
    assert result.exit_code == 1
    assert "is the home folder or above it" in result.stderr


def test_a_package_that_stops_loading_counts_in_attention(
    make_remote: MakeRemote, dotfiles_env: Path
) -> None:
    remote = make_remote()
    subscribe(remote.url, "--add", "starship")
    as_json(["apply", "--yes"])
    publish(remote, {".untaped/dotfiles/starship.yml": "version: 1\nitems: {}\n"})
    run(["apply", "--yes"])
    assert run(["status"]).exit_code == 1
    summary = json.loads((dotfiles_env / "status.json").read_text())
    assert (summary["error"], summary["attention"]) == (1, 1)
    assert (dotfiles_env / "attention").read_text() == "1\n"
