"""``dotfiles show`` at a terminal: the screen saves its changes to config.yml once."""

from __future__ import annotations

import pytest

from dotfiles_test_support import STARSHIP, MakeRemote, as_json, installed, run, subscribe
from untaped.sdk.testing import ScreenKeys, ScriptedTerminal

pytestmark = pytest.mark.usefixtures("dotfiles_env")


def _starship(make_remote: MakeRemote) -> None:
    source = make_remote(packages={"starship": STARSHIP}, files={"starship.toml": "x = 1\n"})
    subscribe(source.url)


def test_the_screen_saves_once_and_says_what_changed(
    make_remote: MakeRemote, terminal: ScriptedTerminal
) -> None:
    _starship(make_remote)
    terminal.screens(ScreenKeys("down", " ", "p", "ctrl-s"))
    result = run(["show"])
    assert result.exit_code == 0, result.output
    assert [s.title for s in terminal.ran] == ["Dotfiles packages"]
    assert result.stdout == ""
    assert "turned on dotfiles/starship (sync)" in result.stderr
    assert "untaped dotfiles apply" in result.stderr
    [row] = as_json(["show", "starship"])
    assert (row["on"], row["policy"], row["chosen"]) == (True, "sync", True)
    ran = installed().cli(["config", "get", "dotfiles.sources.dotfiles.packages.starship"])
    assert "sync" in ran.stdout


def test_quitting_the_screen_writes_nothing(
    make_remote: MakeRemote, terminal: ScriptedTerminal
) -> None:
    _starship(make_remote)
    terminal.screens(ScreenKeys("down", " ", "esc"))
    result = run(["show"])
    assert result.exit_code == 0, result.output
    assert "nothing saved" in result.stderr
    [row] = as_json(["show", "starship"])
    assert row["on"] is False


def test_rows_with_a_format_or_without_a_terminal(
    make_remote: MakeRemote, terminal: ScriptedTerminal
) -> None:
    _starship(make_remote)
    assert [r["name"] for r in as_json(["show"])] == ["starship"]  # no terminal: rows
    terminal.present()
    assert [r["name"] for r in as_json(["show"])] == ["starship"]  # --format json: rows
    assert terminal.ran == []


def test_turning_off_on_the_screen_leaves_what_was_placed_and_says_so(
    make_remote: MakeRemote, terminal: ScriptedTerminal
) -> None:
    _starship(make_remote)
    assert run(["add", "starship"]).exit_code == 0
    assert run(["apply", "--yes"]).exit_code == 0
    terminal.screens(ScreenKeys("down", " ", "ctrl-s"))
    result = run(["show"])
    assert result.exit_code == 0, result.output
    assert "turned off dotfiles/starship" in result.stderr
    assert "what dotfiles/starship placed stays" in result.stderr
    assert "untaped dotfiles remove dotfiles/starship" in result.stderr
    assert "untaped dotfiles apply" not in result.stderr
    [row] = as_json(["show", "starship"])
    assert row["on"] is False
