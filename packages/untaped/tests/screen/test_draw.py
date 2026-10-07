"""The shared drawing helpers: styles from roles, exact-width rows, windows, the edit buffer."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from types import TracebackType

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
from untaped.screen.components.paths import MAX_CANDIDATES, MAX_SCANNED, complete_paths
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
    assert EditBuffer().paste("123\n", allowed="123") == EditBuffer("123", 3)  # a trailing break
    assert EditBuffer("a", 1).paste(" 12 ", allowed="123") == EditBuffer("a12", 3)


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


def test_a_number_paste_is_all_or_nothing_and_trims_its_surroundings() -> None:
    assert EditBuffer("1", 1).paste("1.5", allowed="0123456789") == EditBuffer("1", 1)
    assert EditBuffer("1", 1).paste("1,000", allowed="0123456789") == EditBuffer("1", 1)
    assert EditBuffer().paste("1 000", allowed="0123456789 ") == EditBuffer("1 000", 5)
    assert EditBuffer().paste("1\n2", allowed="0123456789") == EditBuffer()  # an inner break


def test_ctrl_w_removes_the_word_before_the_caret_and_the_spaces_after_it() -> None:
    assert EditBuffer("one two ", 8).key("ctrl-w") == EditBuffer("one ", 4)
    assert EditBuffer("one two   ", 10).key("ctrl-w") == EditBuffer("one ", 4)
    assert EditBuffer("one two", 7).key("ctrl-w") == EditBuffer("one ", 4)
    assert EditBuffer("   ", 3).key("ctrl-w") == EditBuffer("   ", 3)  # nothing to remove
    assert EditBuffer("one two", 4).key("ctrl-w") == EditBuffer("two", 0)


# --- the caret and the ellipsis -----------------------------------------------------


def _reversed_text(line: Text) -> str:
    """The characters of ``line`` drawn with the caret's reverse attribute."""
    return "".join(
        line.plain[span.start : span.end]
        for span in line.spans
        if span.style and "reverse" in str(span.style)
    )


@pytest.mark.parametrize(("inner", "cursor"), [(2, 0), (3, 3), (3, 2), (4, 4), (5, 4), (6, 2)])
def test_the_caret_never_spreads_onto_the_ellipsis(inner: int, cursor: int) -> None:
    line = text_line(DEFAULT, inner, "abcdefghij", cursor, focused=True)

    assert line.cell_len == inner
    assert "\u2026" in line.plain  # the text is cut
    assert "\u2026" not in _reversed_text(line)
    assert len(_reversed_text(line)) == 1  # the caret is one character


@pytest.mark.parametrize("cursor", [10, 9, 8, 7, 5])
def test_moving_the_caret_left_from_the_end_does_not_slide_the_text(cursor: int) -> None:
    text = "abcdefghij"
    line = text_line(DEFAULT, 6, text, cursor, focused=True)
    at_end = text_line(DEFAULT, 6, text, len(text), focused=True)

    assert line.plain == at_end.plain  # the view holds still while the caret stays in it
    assert _reversed_text(line) == (text[cursor] if cursor < len(text) else " ")


def test_the_caret_is_always_visible_and_the_row_is_exactly_as_wide() -> None:
    text = "abcdefghijklmnopqrstuvwxyz"
    for inner in (1, 2, 3, 5, 8, 20):
        for cursor in range(len(text) + 1):
            line = text_line(DEFAULT, inner, text, cursor, focused=True)
            assert line.cell_len == inner
            if inner >= 3:
                assert len(_reversed_text(line)) == 1, (inner, cursor, line.plain)


def test_wide_characters_scroll_by_cells_not_characters() -> None:
    text = "\u65e5\u672c\u8a9e\u30c6\u30b9\u30c8" * 3  # 18 characters, 36 cells
    for inner in (9, 12, 15):
        for cursor in (0, 5, 9, 17, 18):
            line = text_line(DEFAULT, inner, text, cursor, focused=True)
            assert line.cell_len == inner, (inner, cursor)
            assert _reversed_text(line)  # the caret shows
    at_end = text_line(DEFAULT, 9, text, len(text), focused=True)
    assert at_end.plain == text[-4:] + " "  # four 2-cell characters and the caret cell
    assert _reversed_text(at_end) == " "


# --- completing paths ---------------------------------------------------------------


class _Entry:
    def __init__(self, name: str, *, directory: bool | OSError) -> None:
        self.name = name
        self._directory = directory

    def is_dir(self) -> bool:
        if isinstance(self._directory, OSError):
            raise self._directory
        return self._directory


class _Listing:
    """What ``os.scandir`` returns, over ``entries``, counting how many were pulled."""

    def __init__(self, entries: Iterator[_Entry]) -> None:
        self._entries = entries
        self.pulled = 0

    def __enter__(self) -> _Listing:
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    def __iter__(self) -> Iterator[_Entry]:
        for entry in self._entries:
            self.pulled += 1
            yield entry


def test_completion_puts_directories_first_whatever_order_the_filesystem_lists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("b_dir", "d_dir"):
        (tmp_path / name).mkdir()
    for name in ("a_file", "c_file"):
        (tmp_path / name).write_text("x")
    real = os.scandir
    expected = [f"{tmp_path}/{name}" for name in ("b_dir/", "d_dir/", "a_file", "c_file")]

    for reverse in (False, True):

        def listing(path: str, *, reverse: bool = reverse) -> _Listing:
            with real(path) as entries:
                found = sorted(entries, key=lambda entry: entry.name, reverse=reverse)
            return _Listing(iter(found))  # type: ignore[arg-type]

        monkeypatch.setattr(os, "scandir", listing)
        assert list(complete_paths(f"{tmp_path}/")) == expected


def test_completion_stops_scanning_a_huge_directory_and_still_caps_its_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listing = _Listing(_Entry(f"item{n:05d}", directory=False) for n in range(5 * MAX_SCANNED))
    monkeypatch.setattr(os, "scandir", lambda path: listing)

    found = complete_paths("/some/dir/item")

    assert len(found) == MAX_CANDIDATES
    assert listing.pulled <= MAX_SCANNED + 1  # it never walked the other four fifths


def test_one_entry_that_cannot_be_inspected_does_not_hide_the_others(tmp_path: Path) -> None:
    (tmp_path / "loop").symlink_to("loop")  # is_dir() raises "too many levels of symbolic links"
    (tmp_path / "real").mkdir()
    (tmp_path / "file").write_text("x")

    assert complete_paths(f"{tmp_path}/") == (
        f"{tmp_path}/real/",
        f"{tmp_path}/file",
        f"{tmp_path}/loop",
    )


def test_an_entry_whose_inspection_fails_is_offered_as_a_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listing = _Listing(
        iter([_Entry("a", directory=PermissionError("no")), _Entry("b", directory=True)])
    )
    monkeypatch.setattr(os, "scandir", lambda path: listing)

    assert complete_paths("/x/") == ("/x/b/", "/x/a")
