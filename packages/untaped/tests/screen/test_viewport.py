"""``Viewport``, ``window`` and ``windowed``: a long list or form is cut to what is on screen."""

from __future__ import annotations

import pytest
from rich.text import Text

from untaped.screen.components.layout import Viewport, window, windowed
from untaped.screen.core import Key, Paste
from untaped.stability import Experimental, function_mark
from untaped.testing.screens import rendered_text


def test_a_viewport_shows_the_rows_from_its_offset() -> None:
    assert list(Viewport(100, 10).shown(5)) == [10, 11, 12, 13, 14]
    assert list(Viewport(3).shown(5)) == [0, 1, 2]  # short content: all of it
    assert list(Viewport(0).shown(5)) == []
    assert list(Viewport(10).shown(0)) == []


def test_the_offset_never_runs_past_the_end() -> None:
    assert Viewport(20, 99).start(5) == 15
    assert list(Viewport(20, 99).shown(5)) == [15, 16, 17, 18, 19]
    assert Viewport(2, 5).start(5) == 0


@pytest.mark.parametrize(
    ("cursor", "start"),
    [(0, 0), (3, 0), (50, 45), (99, 90), (98, 90)],  # centred, stopping at either end
)
def test_a_centred_viewport_keeps_the_cursor_in_the_middle(cursor: int, start: int) -> None:
    viewport = Viewport(100).centred(cursor, 10)

    assert viewport.start(10) == start
    assert cursor in viewport.shown(10)


def test_around_leaves_a_block_that_fits_at_the_top_alone() -> None:
    assert Viewport(40).around(2, 6, 10).start(10) == 0
    assert Viewport(40).around(0, 10, 10).start(10) == 0


def test_around_does_not_scroll_for_a_block_ending_exactly_at_the_window_bottom() -> None:
    assert Viewport(40, 5).around(7, 10, 10).start(10) == 0


def test_around_centres_a_block_below_the_fold() -> None:
    viewport = Viewport(40).around(20, 24, 10)

    shown = viewport.shown(10)
    assert 20 in shown
    assert 23 in shown
    assert shown.start == 17  # four rows in the middle of ten


def test_around_shows_the_top_of_a_block_taller_than_the_window() -> None:
    assert Viewport(60).around(30, 55, 10).start(10) == 30


def test_around_keeps_the_last_block_in_sight_at_the_end() -> None:
    shown = Viewport(40).around(35, 40, 10).shown(10)

    assert (shown.start, shown.stop) == (30, 40)


def test_scrolling_moves_by_rows_and_stays_inside_the_content() -> None:
    assert Viewport(30, 5).scrolled(3, 10).offset == 8
    assert Viewport(30, 5).scrolled(-9, 10).offset == 0
    assert Viewport(30, 5).scrolled(99, 10).offset == 20


def test_keys_scroll_and_anything_else_is_the_same_object() -> None:
    viewport = Viewport(30, 5)

    assert viewport.update(Key("down"), 10).offset == 6
    assert viewport.update(Key("up"), 10).offset == 4
    assert viewport.update(Key("home"), 10).offset == 0
    assert viewport.update(Key("end"), 10).offset == 20
    for message in (Key("a"), Key("enter"), Paste("x"), "text"):
        assert viewport.update(message, 10) is viewport
    assert Viewport(30).update(Key("up"), 10) == Viewport(30)  # at the top: nothing to do


def test_window_slices_a_long_list_around_the_cursor() -> None:
    items = list(range(2000))

    first, shown = window(items, 1000, 10)

    assert first == 995
    assert list(shown) == list(range(995, 1005))
    assert window(items, 0, 10)[0] == 0
    assert window(items, 1999, 10)[0] == 1990
    assert window([], 0, 10) == (0, [])
    assert window(["a", "b"], 1, 10) == (0, ["a", "b"])


def test_windowed_stacks_blocks_with_a_gap_and_shows_all_when_they_fit() -> None:
    blocks = [Text("one"), Text("two\nthree"), Text("four")]

    assert rendered_text(windowed(blocks, 0, 20), 10, 20).splitlines() == [
        "one",
        "",
        "two",
        "three",
        "",
        "four",
    ]


def test_windowed_cuts_to_the_rows_that_keep_the_focused_block() -> None:
    blocks = [Text(f"block {n}") for n in range(12)]

    top = rendered_text(windowed(blocks, 0, 6, gap=0), 10, 20).splitlines()
    low = rendered_text(windowed(blocks, 8, 6, gap=0), 10, 20).splitlines()

    assert len(top) == len(low) == 6
    assert top[0] == "block 0"
    assert "block 8" in low
    assert "block 0" not in low


def test_windowed_with_no_blocks_draws_nothing_and_an_out_of_range_focus_is_clamped() -> None:
    assert rendered_text(windowed([], 0, 5), 10, 5) == ""
    assert rendered_text(windowed([Text("only")], 7, 5), 10, 5) == "only"


def test_viewport_is_marked_experimental() -> None:
    assert isinstance(function_mark(Viewport), Experimental)
