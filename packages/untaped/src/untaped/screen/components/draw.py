"""Drawing helpers the components share: styles from roles, exact-width rows, windows.

Every glyph, colour and box a component draws comes from the :class:`Frame` it
is given; this module is where a role name becomes a Rich style and where a
row of text is cut or padded to the cells it has. The two emphasis attributes
below (bold, reverse) are the only styling that is not a theme role: the
cursor is by definition the inversion of the text under it, and emphasis
layers over whatever colour the theme chose.
"""

from __future__ import annotations

from collections.abc import Sequence

from rich.cells import cell_len
from rich.errors import StyleSyntaxError
from rich.style import Style
from rich.text import Text

from untaped.screen.core import Frame
from untaped.screen.fit import fit_text

__all__ = [
    "BOLD",
    "CARET",
    "DIM",
    "divider",
    "inner_width",
    "option_row",
    "role_style",
    "text_line",
    "window_start",
]

BOLD = Style(bold=True)
DIM = Style(dim=True)
#: The caret and the character it sits on: the text colour inverted.
CARET = Style(reverse=True)


def role_style(frame: Frame, *roles: str) -> Style:
    """The Rich style of the screen ``roles`` layered left to right (the later one wins).

    A role the user set to something Rich cannot parse styles nothing instead of
    raising in the middle of a frame.
    """
    combined = Style.null()
    for role in roles:
        try:
            combined += Style.parse(frame.style(role) or "none")
        except StyleSyntaxError:
            continue
    return combined


def inner_width(frame: Frame, width: int | None) -> int:
    """The cells inside a field box that is ``width`` wide (the frame's width when ``None``)."""
    total = width or frame.width
    return max(1, total - 4) if frame.box() is not None else max(1, total)


def divider(frame: Frame, inner: int) -> Text | None:
    """A rule across the box, drawn with the box's own horizontal; ``None`` without a box."""
    outline = frame.box()
    if outline is None:
        return None
    return Text(outline.row_horizontal * inner, style=role_style(frame, "screen.border"))


def window_start(total: int, current: int, size: int) -> int:
    """First index of a ``size``-row window over ``total`` rows that centres ``current``."""
    return max(0, min(current - size // 2, total - size))


def option_row(
    frame: Frame,
    inner: int,
    *,
    lead: Sequence[tuple[str, Style]] = (),
    label: str,
    label_style: Style,
    trail: str = "",
    trail_style: Style | None = None,
    base: Style | None = None,
) -> Text:
    """One row of exactly ``inner`` cells: ``lead`` glyphs, the label, a right-aligned ``trail``.

    ``base`` styles the whole row, padding included, so the cursor row's
    highlight reaches both edges. The label is cut with the theme's ellipsis
    when the row is too narrow; the trail is dropped first when it does not fit.
    """
    line = Text(style=base or Style.null())
    used = 0
    for glyph, style in lead:
        line.append(glyph, style=style)
        used += cell_len(glyph)
    room = inner - used
    trail_cells = cell_len(trail)
    if trail and room > trail_cells + 2:
        room -= trail_cells + 1
    else:
        trail = ""
    line.append_text(fit_text(Text(label, style=label_style), max(room, 0), frame.ellipsis()))
    if trail:
        line.append(" ")
        line.append(trail, style=trail_style or Style.null())
    return fit_text(line, inner, frame.ellipsis())


def text_line(
    frame: Frame,
    inner: int,
    text: str,
    cursor: int,
    *,
    focused: bool,
    placeholder: str = "",
) -> Text:
    """The value line of a text field: ``text`` with the drawn caret, scrolled to keep it in view.

    Unfocused, the value starts at its first character and is cut with the
    ellipsis; focused, it scrolls so the caret (a reversed character, or a
    reversed space past the end) always shows. An empty value shows the
    placeholder in the muted style.
    """
    muted = role_style(frame, "screen.muted")
    value = role_style(frame, "screen.value")
    if not text:
        line = Text()
        shown = placeholder
        if focused:
            line.append(shown[:1] or " ", style=value + CARET)
            shown = shown[1:]
        line.append(shown, style=muted)
        return fit_text(line, inner, frame.ellipsis())
    start = 0
    if focused:
        room = inner - 2 if inner >= 3 else max(inner - 1, 0)
        while start < cursor and cell_len(text[start:cursor]) > room:
            start += 1
    visible = text[start:]
    line = Text(visible, style=value)
    if focused:
        offset = cursor - start
        if offset < len(visible):
            line.stylize(CARET, offset, offset + 1)
        else:
            line.append(" ", style=CARET)
    return fit_text(line, inner, frame.ellipsis())
