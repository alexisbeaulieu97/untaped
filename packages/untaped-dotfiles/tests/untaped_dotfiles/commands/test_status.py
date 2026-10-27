"""``dotfiles status``, ``summary`` and ``diff`` end to end: the state of each placed path."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dotfiles_test_support import (
    Upstream,
    bump,
    json_rows,
    run,
    states,
    subscribe,
)

pytestmark = pytest.mark.usefixtures("dotfiles_env")


def test_status_before_apply_is_pending_and_writes_the_status_files(
    make_upstream: Upstream, dotfiles_env: Path
) -> None:
    bare, _ = make_upstream()
    subscribe(bare)
    run(["enable", "fish", "starship"])
    assert states() == {
        "fish/config.fish": "pending",
        "fish/conf.d/abbr.fish": "pending",
        "fish/conf.d/path.fish": "pending",
        "starship.toml": "pending",
    }
    assert json.loads((dotfiles_env / "status.json").read_text())["pending"] == 4
    assert (dotfiles_env / "attention").read_text() == "0\n"
    assert run(["status", "--check"]).exit_code == 0
    summary = run(["summary", "--format", "json"])
    assert json.loads(summary.stdout)["total"] == 4


def _unreadable(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    """Make ``path`` unreadable, root or not: permission bits do not stop root."""
    read_bytes = Path.read_bytes

    def refuse(self: Path) -> bytes:
        if self == path:
            raise PermissionError(13, "Permission denied", str(self))
        return read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", refuse)


def test_an_unreadable_placed_path_exits_1_and_counts_in_attention(
    make_upstream: Upstream, user_home: Path, dotfiles_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bare, _ = make_upstream()
    subscribe(bare)
    run(["enable", "starship"])
    run(["apply", "--yes"])
    target = user_home / ".config" / "starship.toml"
    _unreadable(monkeypatch, target)
    result = run(["status", "--check", "--format", "json"])
    assert result.exit_code == 1, result.output
    [row] = json_rows(result)
    assert row["state"] == "error" and row["error"] is not None
    assert str(row["detail"]).startswith(f"could not read {target}: ")
    assert (dotfiles_env / "attention").read_text() == "1\n"


def test_a_dry_run_over_an_unreadable_placed_path_fails_like_the_run(
    make_upstream: Upstream, user_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bare, _ = make_upstream()
    subscribe(bare)
    run(["enable", "starship"])
    run(["apply", "--yes"])
    _unreadable(monkeypatch, user_home / ".config" / "starship.toml")

    result = run(["apply", "--dry-run", "--format", "json"])

    assert result.exit_code == 1, result.output
    [row] = json_rows(result)
    assert row["action"] == "failed" and row["error"] is not None


def test_an_unreadable_source_in_a_checkout_is_an_error_row(
    make_upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, author = make_upstream()
    subscribe(author, "--name", "local")
    run(["enable", "starship"])
    source = (author / "starship.toml").resolve()
    _unreadable(monkeypatch, source)
    result = run(["status", "--check", "--format", "json"])
    assert result.exit_code == 1, result.output
    [row] = json_rows(result)
    assert row["state"] == "error"
    assert str(row["detail"]).startswith("could not read source starship.toml: ")
    diff = run(["diff"])
    assert diff.exit_code == 1 and "error: starship/starship.toml: could not read" in diff.stderr
    assert run(["apply", "--yes"]).exit_code == 1


def test_diff_shows_copy_and_merge_changes(make_upstream: Upstream, user_home: Path) -> None:
    bare, author = make_upstream()
    subscribe(bare)
    run(["enable", "starship", "claude", "--policy", "manual"])
    run(["apply", "--yes"])
    assert run(["diff"]).stdout == ""
    bump(author, {"starship.toml": "new\n", "claude/settings.json": '{"theme": "light"}\n'})
    run(["sync"])
    out = run(["diff"]).stdout
    assert "--- a/~/.config/starship.toml" in out and "+new" in out
    assert '+  "theme": "light"' in out and '-  "theme": "dark"' in out
    assert "SKILL.md" not in out


def test_an_item_gone_from_the_manifest_is_reported_not_fatal(
    make_upstream: Upstream, user_home: Path
) -> None:
    bare, author = make_upstream()
    subscribe(bare)
    run(["enable", "starship", "fish"])
    run(["apply", "--yes"])
    only_config = "files: [{source: fish/config.fish, target: ~/.config/fish/config.fish}]"
    bump(author, {"dotfiles.yml": f"items:\n  fish:\n    {only_config}\n"})
    result = run(["sync", "--format", "json"])
    assert result.exit_code == 1, result.output
    assert "'starship' is no longer in the manifest" in result.stderr
    assert "disable starship" in result.stderr
    rows = {str(r["source"]): r for r in json_rows(result)}
    assert set(rows) == {"fish/config.fish", "fish/conf.d/abbr.fish", "fish/conf.d/path.fish"}
    assert rows["fish/conf.d/abbr.fish"]["state"] == "orphan"


@pytest.mark.parametrize("args", [["apply"], ["remove", "--all"]], ids=["apply", "remove"])
def test_a_dry_run_fails_on_the_problem_the_real_run_fails_on(
    make_upstream: Upstream, args: list[str]
) -> None:
    bare, author = make_upstream()
    subscribe(bare)
    run(["enable", "starship", "fish"])
    run(["apply", "--yes"])
    only_config = "files: [{source: fish/config.fish, target: ~/.config/fish/config.fish}]"
    bump(author, {"dotfiles.yml": f"items:\n  fish:\n    {only_config}\n"})
    run(["sync", "--format", "json"])

    result = run([*args, "--dry-run", "--format", "json"])

    assert result.exit_code == 1, result.output
    assert "'starship' is no longer in the manifest" in result.stderr


def test_status_of_an_unknown_enabled_item_hints_enable(make_upstream: Upstream) -> None:
    bare, _ = make_upstream()
    subscribe(bare)
    result = run(["status", "fish"])
    assert result.exit_code == 1
    assert "enabled item not found: 'fish'" in result.stderr
    assert "hint: run `untaped dotfiles enable fish`" in result.stderr
