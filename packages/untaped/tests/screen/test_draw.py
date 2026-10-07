"""The shared drawing helpers: styles from roles, exact-width rows, windows, the edit buffer."""

from __future__ import annotations

from pathlib import Path

import pytest
from rich.cells import cell_len
from rich.style import Style
from rich.text import Text

from screen.gallery import render_styled
from untaped.screen.components.box import field_box
from untaped.screen.components.draw import (
    divider,
    inner_width,
    option_row,
    role_style,
    text_line,
    window_start,
)
from untaped.screen.components.paths import MAX_CANDIDATES, complete_paths
from untaped.screen.components.text import EditBuffer
from untaped.screen.core import Frame
from untaped.theme import BUILTIN_THEMES, ThemeSpec

DEFAULT = Frame(60, 20, BUILTIN_THEMES["default"])


def test_role_style_layers_roles_left_to_right() -> None:
    style = role_style(DEFAULT, "screen.highlight", "screen.success")

    assert style.bold  # from the highlight
    assert style.bgcolor is not None  # the highlight's fill stays
    assert (
        style.color == role_style(DEFAULT, "screen.success").color
    )  # the later role's text colour


def test_role_style_ignores_a_role_the_user_set_to_nonsense() -> None:
    theme = ThemeSpec(color_roles={"screen.accent": "no such style"})

    assert role_style(Frame(40, 10, theme), "screen.accent") == Style.null()


def test_role_style_refuses_a_name_that_is_no_screen_role() -> None:
    with pytest.raises(ValueError, match="unknown screen color role"):
        role_style(DEFAULT, "header")


def test_inner_width_leaves_room_for_the_border_and_padding_only_with_a_box() -> None:
    assert inner_width(DEFAULT, 40) == 36
    assert inner_width(DEFAULT, None) == 56
    assert inner_width(Frame(60, 20, BUILTIN_THEMES["quiet"]), 40) == 40
    assert inner_width(DEFAULT, 2) == 1


def test_the_divider_uses_the_themes_horizontal_and_is_absent_without_a_box() -> None:
    assert divider(DEFAULT, 5) == Text("─────")
    assert divider(Frame(60, 20, BUILTIN_THEMES["plain"]), 5) == Text("-----")
    assert divider(Frame(60, 20, BUILTIN_THEMES["quiet"]), 5) is None


@pytest.mark.parametrize(
    ("total", "current", "size", "start"),
    [(10, 0, 5, 0), (10, 5, 5, 3), (10, 9, 5, 5), (3, 1, 5, 0)],
)
def test_window_start_keeps_the_current_row_inside_the_window(
    total: int, current: int, size: int, start: int
) -> None:
    assert window_start(total, current, size) == start


@pytest.mark.parametrize("inner", [40, 20, 12, 8, 4, 1])
def test_an_option_row_is_exactly_as_wide_as_it_is_given(inner: int) -> None:
    row = option_row(
        DEFAULT,
        inner,
        lead=[("▶", Style()), (" ", Style())],
        label="a long label that does not fit",
        label_style=Style(),
        trail="detail",
    )

    assert row.cell_len == inner


def test_an_option_row_right_aligns_its_trail_and_drops_it_when_it_does_not_fit() -> None:
    wide = option_row(DEFAULT, 20, label="name", label_style=Style(), trail="detail")
    assert wide.plain == "name" + " " * 10 + "detail"
    narrow = option_row(DEFAULT, 8, label="name", label_style=Style(), trail="detail")
    assert narrow.plain == "name    "


def test_an_option_row_fills_its_whole_width_with_the_base_style() -> None:
    base = Style(bgcolor="blue")
    row = option_row(DEFAULT, 10, label="ab", label_style=Style(bold=True), base=base)

    assert row.style == base
    assert row.plain == "ab        "


def test_an_empty_focused_line_reverses_the_first_placeholder_character() -> None:
    focused = text_line(DEFAULT, 20, "", 0, focused=True, placeholder="name")
    assert focused.plain.rstrip() == "name"
    assert any(span.style and "reverse" in str(span.style) for span in focused.spans)
    assert text_line(DEFAULT, 20, "", 0, focused=False).plain == " " * 20


def test_the_edit_buffer_clamps_and_edits() -> None:
    assert EditBuffer("abc", 9) == EditBuffer("abc", 3)
    assert EditBuffer("abc", -2).cursor == 0
    buffer = EditBuffer("ab", 1)
    assert buffer.key("x") == EditBuffer("axb", 2)
    assert buffer.key("backspace") == EditBuffer("b", 0)
    assert buffer.key("delete") == EditBuffer("a", 1)
    assert buffer.key("home") == EditBuffer("ab", 0)
    assert buffer.key("end") == EditBuffer("ab", 2)
    assert buffer.key("ctrl-u") == EditBuffer()
    assert buffer.key("enter") is None
    assert buffer.key("ctrl-r") is None
    assert buffer.key("é") == EditBuffer("aéb", 2)


def test_the_edit_buffer_filters_what_a_number_may_hold() -> None:
    assert EditBuffer().key("a", allowed="123") == EditBuffer()
    assert EditBuffer().paste("1x2\n3", allowed="123") == EditBuffer("123", 3)


def test_field_box_labels_the_top_border_and_puts_help_below() -> None:
    segments = render_styled(
        field_box(DEFAULT, "Name", Text("body"), help="Some help.", width=20), width=30
    )
    text = "".join(segment.text for segment in segments)

    assert text.splitlines()[0].startswith("╭─ Name ")
    assert "Some help." in text.splitlines()[-1]


def test_field_box_wraps_a_long_note_within_its_own_width() -> None:
    segments = render_styled(
        field_box(DEFAULT, "N", Text("b"), help="word " * 10, width=20), width=40
    )
    rows = "".join(segment.text for segment in segments).splitlines()

    assert max(cell_len(row.rstrip()) for row in rows) <= 20
    assert len(rows) > 4


def test_complete_paths_is_capped_and_survives_a_missing_directory(tmp_path: Path) -> None:
    for index in range(MAX_CANDIDATES + 5):
        (tmp_path / f"f{index:03d}").write_text("")

    assert len(complete_paths(f"{tmp_path}/f")) == MAX_CANDIDATES
    assert complete_paths(f"{tmp_path}/nope/f") == ()
    assert complete_paths("") == ()
