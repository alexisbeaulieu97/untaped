"""``fit_text`` cuts and pads one line to an exact width in terminal cells."""

from __future__ import annotations

import pytest
from rich.cells import cell_len
from rich.text import Text

from untaped.screen.fit import fit_text


def _fit(text: str | Text, width: int, ellipsis: str = "…") -> Text:
    return fit_text(Text(text) if isinstance(text, str) else text, width, ellipsis)


def test_short_text_is_padded_to_the_width() -> None:
    assert _fit("abc", 6).plain == "abc   "


def test_text_of_exactly_the_width_is_untouched() -> None:
    assert _fit("abcdef", 6).plain == "abcdef"


def test_long_text_is_cut_with_the_ellipsis() -> None:
    assert _fit("abcdefgh", 6).plain == "abcde…"


def test_wide_glyphs_count_their_cells() -> None:
    fitted = _fit("中文中文", 5)  # four double-width characters
    assert cell_len(fitted.plain) == 5
    assert fitted.plain.endswith("…")


def test_a_wide_glyph_on_the_cut_never_overflows() -> None:
    for width in range(1, 9):
        assert cell_len(_fit("a中b中c中", width).plain) == width


def test_an_ellipsis_token_wider_than_a_cell() -> None:
    assert _fit("abcdefgh", 6, "...").plain == "abc..."
    assert _fit("abc", 6, "...").plain == "abc   "


def test_a_token_wider_than_the_width_is_cropped() -> None:
    assert _fit("abcdefgh", 2, "...").plain == ".."
    assert _fit("abcdefgh", 3, "...").plain == "..."


@pytest.mark.parametrize("width", [0, -3])
def test_width_zero_or_less_is_empty(width: int) -> None:
    assert _fit("abc", width).plain == ""


def test_width_one_keeps_the_ellipsis_only() -> None:
    assert _fit("abc", 1).plain == "…"
    assert _fit("a", 1).plain == "a"


def test_an_empty_ellipsis_just_cuts() -> None:
    assert _fit("abcdefgh", 4, "").plain == "abcd"


def test_the_input_is_not_changed() -> None:
    text = Text("abcdefgh", style="bold")
    fit_text(text, 4, "…")
    assert text.plain == "abcdefgh"


def test_the_ellipsis_keeps_the_style_of_the_cut_text() -> None:
    text = Text("abcdefgh")
    text.stylize("red", 0, 8)
    fitted = _fit(text, 5)
    assert [(span.start, span.end, span.style) for span in fitted.spans] == [(0, 5, "red")]


def test_a_style_that_ends_before_the_cut_is_not_extended() -> None:
    text = Text("abcdefgh")
    text.stylize("red", 0, 2)
    fitted = _fit(text, 5)
    assert [(span.start, span.end) for span in fitted.spans] == [(0, 2)]
