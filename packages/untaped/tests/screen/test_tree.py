"""``Tree``: expandable rows, parent navigation, windowing and the theme tokens it draws with."""

from __future__ import annotations

import pytest

from screen.gallery import field_of, lines, render_styled, role, run_solo, style_of
from untaped.screen.components.lists import Tree, TreeRow
from untaped.screen.core import Cancel, Frame, Key
from untaped.stability import Experimental, function_mark
from untaped.theme import BUILTIN_THEMES

THEMES = sorted(BUILTIN_THEMES)
DEFAULT = BUILTIN_THEMES["default"]
ROWS = (
    TreeRow(
        "all",
        "all items",
        "3 set",
        (
            TreeRow("all.branch", "branch", "main"),
            TreeRow(
                "all.sync",
                "sync",
                "",
                (TreeRow("all.sync.pull", "pull", "ff"), TreeRow("all.sync.push", "push", "no")),
            ),
        ),
    ),
    TreeRow("acme/api", "acme/api", "inherit", (TreeRow("api.branch", "branch", "dev"),)),
    TreeRow("acme/web", "acme/web"),
)


def _tree(**kwargs: object) -> Tree:
    return Tree("Settings", ROWS, **kwargs)  # type: ignore[arg-type]


def _text(run) -> list[str]:  # type: ignore[no-untyped-def]
    return lines(run.frame)


# --- drawing -----------------------------------------------------------------------


def test_a_closed_tree_shows_its_top_rows_with_the_closed_symbol_and_summaries() -> None:
    run = run_solo(_tree())

    assert _text(run)[0].startswith("╭─ Settings")
    assert "▸ all items" in run.frame
    assert "3 set" in run.frame
    assert "▸ acme/api" in run.frame
    assert "  acme/web" in run.frame  # a leaf has no marker, the label lines up
    assert "branch" not in run.frame


def test_an_expanded_row_shows_its_children_indented_under_the_open_symbol() -> None:
    run = run_solo(_tree(expanded=frozenset({"all", "all.sync"})))

    body = _text(run)
    assert any(line.startswith("│ ▾ all items") for line in body)
    assert any(line.startswith("│     branch") for line in body)  # depth 1: two cells in
    assert any(line.startswith("│   ▾ sync") for line in body)
    assert any(line.startswith("│       pull") for line in body)  # depth 2: four cells in
    assert "ff" in run.frame


def test_the_cursor_row_is_highlighted_and_only_while_focused() -> None:
    fill = role(DEFAULT, "screen.highlight").bgcolor

    def filled(focused: bool) -> list[str]:
        segments = render_styled(
            _tree(cursor=1).view(Frame(60, 20, DEFAULT), focused=focused, width=40), width=40
        )
        return [s.text for s in segments if s.style is not None and s.style.bgcolor == fill]

    assert any("acme/api" in text for text in filled(True))
    assert filled(False) == []


def test_summaries_are_muted_and_labels_are_the_value_colour() -> None:
    segments = render_styled(_tree().view(Frame(60, 20, DEFAULT), width=40), width=40)

    assert style_of(segments, "3 set").color == role(DEFAULT, "screen.muted").color
    assert style_of(segments, "acme/web").color == role(DEFAULT, "screen.value").color


# --- keys --------------------------------------------------------------------------


def test_right_opens_a_closed_row_then_steps_into_it_and_left_walks_back_out() -> None:
    run = run_solo(_tree(), "right")
    assert field_of(run).expanded == frozenset({"all"})
    assert field_of(run).cursor == 0

    run = run_solo(_tree(), "right", "right")
    assert field_of(run).value == "all.branch"

    run = run_solo(_tree(), "right", "right", "left")  # a leaf: left goes to the parent
    assert field_of(run).value == "all"
    assert field_of(run).expanded == frozenset({"all"})

    run = run_solo(_tree(), "right", "left")  # an open row: left closes it
    assert field_of(run).expanded == frozenset()


def test_enter_toggles_a_row_that_has_children_and_leaves_a_leaf_to_the_form() -> None:
    run = run_solo(_tree(), "enter")
    assert field_of(run).expanded == frozenset({"all"})
    assert run.model.unhandled == ()

    assert field_of(run_solo(_tree(), "enter", "enter")).expanded == frozenset()

    leaf = run_solo(_tree(), "end", "enter")
    assert field_of(leaf).value == "acme/web"
    assert len(leaf.model.unhandled) == 1


def test_up_down_home_and_end_move_over_the_visible_rows_only() -> None:
    assert field_of(run_solo(_tree(), "down", "down")).value == "acme/web"
    assert field_of(run_solo(_tree(), "end")).value == "acme/web"
    opened = _tree(expanded=frozenset({"all"}))
    assert field_of(run_solo(opened, "down")).value == "all.branch"
    assert field_of(run_solo(opened, "end", "home")).value == "all"


def test_collapsing_keeps_the_cursor_on_the_closed_row() -> None:
    run = run_solo(_tree(expanded=frozenset({"all"})), "left")

    assert field_of(run).value == "all"
    assert field_of(run).expanded == frozenset()


@pytest.mark.parametrize("key", ["left", "right", "tab", "a", "esc", "ctrl-s"])
def test_a_key_a_leaf_cannot_use_is_left_to_the_parent(key: str) -> None:
    leaf = _tree(cursor=2)

    assert leaf.update(Key(key))[0] is leaf


def test_the_same_object_comes_back_when_nothing_changes() -> None:
    tree = _tree()

    assert tree.update(Key("up"))[0] is tree
    assert tree.update("text")[0] is tree
    assert tree.update(Key("left"))[0] is tree  # a closed top row has no parent
    empty = Tree("Empty", ())
    assert empty.update(Key("down"))[0] is empty
    assert empty.value == ""
    assert run_solo(tree, "esc").outcome == Cancel()


def test_the_cursor_is_kept_inside_the_visible_rows() -> None:
    assert _tree(cursor=99).cursor == 2
    assert _tree(cursor=-4).cursor == 0
    assert Tree("Empty", (), cursor=3).cursor == 0


# --- long trees --------------------------------------------------------------------


def test_a_long_tree_shows_only_the_window_around_the_cursor() -> None:
    rows = tuple(TreeRow(f"r{n:03d}", f"row {n:03d}") for n in range(500))
    run = run_solo(Tree("Many", rows), "end")

    assert "row 499" in run.frame
    assert "row 000" not in run.frame
    assert len(lines(run.frame)) <= 20
    middle = run_solo(Tree("Many", rows), *["down"] * 250)
    assert "row 250" in middle.frame
    assert "row 245" in middle.frame


# --- look --------------------------------------------------------------------------


def test_help_and_error_follow_the_box_like_every_field() -> None:
    run = run_solo(_tree(help="Pick a setting."))
    assert "Pick a setting." in run.frame

    errored = render_styled(
        _tree().with_error("Nope.").view(Frame(60, 20, DEFAULT), width=40), width=40
    )
    assert style_of(errored, "Nope.").color == role(DEFAULT, "screen.error").color
    assert _tree().validate() == ""


def test_the_plain_theme_draws_the_tree_with_ascii_symbols() -> None:
    plain = BUILTIN_THEMES["plain"]
    run = run_solo(_tree(expanded=frozenset({"all"})), theme=plain)

    assert "v all items" in run.frame
    assert "> acme/api" in run.frame
    assert run.frame.isascii()


def test_without_a_box_the_tree_is_its_label_and_rows() -> None:
    run = run_solo(_tree(), theme=BUILTIN_THEMES["quiet"])

    assert lines(run.frame)[0] == "Settings"
    assert not any(char in run.frame for char in "╭│╰")


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("size", [(100, 30), (60, 20), (24, 10)])
def test_every_builtin_theme_renders_the_tree(theme: str, size: tuple[int, int]) -> None:
    for component in (_tree(expanded=frozenset({"all", "all.sync"})), Tree("Empty", ())):
        for focused in (True, False):
            run = run_solo(component, theme=BUILTIN_THEMES[theme], size=size, focused=focused)
            assert all(len(line) <= size[0] for line in run.frame.splitlines())


def test_tree_and_tree_row_are_marked_experimental() -> None:
    assert isinstance(function_mark(Tree), Experimental)
    assert isinstance(function_mark(TreeRow), Experimental)
