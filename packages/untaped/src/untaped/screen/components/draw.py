"""Drawing helpers the components share: styles from roles, exact-width rows, windows.

Every glyph, colour and box a component draws comes from the :class:`Frame` it
is given; this module is where a role name becomes a Rich style and where a
row of text is cut or padded to the cells it has. The caret is the
``screen.caret`` role (reverse by default) and bold emphasis is
``screen.emphasis``, both layered over whatever colour the theme chose.
:data:`DIM` is the one attribute that is not a role.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import replace

from rich.cells import cell_len
from rich.errors import StyleSyntaxError
from rich.style import Style
from rich.text import Text

from untaped.screen.core import Frame
from untaped.screen.fit import fit_text

__all__ = [
    "DIM",
    "divider",
    "inner_width",
    "option_row",
    "role_style",
    "text_line",
    "unboxed",
    "window_start",
]

#: The one attribute that is not a theme role: a dimmed item is the muted colour with the
#: terminal's dim attribute over it, so it stays a step apart from a plain muted one on any theme.
DIM = Style(dim=True)


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


def unboxed(frame: Frame) -> Frame:
    """``frame`` with the border off: what a component drawn inside another box is given."""
    return replace(frame, theme=frame.theme.model_copy(update={"border": "none"}))


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
    marks: Collection[int] = (),
) -> Text:
    """One row of exactly ``inner`` cells: ``lead`` glyphs, the label, a right-aligned ``trail``.

    ``base`` styles the whole row, padding included, so the cursor row's
    highlight reaches both edges. ``marks`` are positions in ``label`` drawn
    with the ``screen.match`` role (what a search matched). The label is cut with the
    theme's ellipsis when the row is too narrow; the trail is dropped first when
    it does not fit.
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
    shown = Text(label, style=label_style)
    match = role_style(frame, "screen.match")
    for first, stop in _runs(marks, len(label)):
        shown.stylize(match, first, stop)
    line.append_text(fit_text(shown, max(room, 0), frame.ellipsis()))
    if trail:
        line.append(" ")
        line.append(trail, style=trail_style or Style.null())
    return fit_text(line, inner, frame.ellipsis())


def _runs(positions: Collection[int], length: int) -> list[tuple[int, int]]:
    """``positions`` inside ``length`` as ``(first, stop)`` runs of neighbouring characters."""
    runs: list[tuple[int, int]] = []
    for position in sorted(p for p in positions if 0 <= p < length):
        if runs and runs[-1][1] == position:
            runs[-1] = (runs[-1][0], position + 1)
        else:
            runs.append((position, position + 1))
    return runs


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
    caret = role_style(frame, "screen.caret")
    on_caret = role_style(frame, "screen.value", "screen.caret")
    if not text:
        line = Text()
        shown = placeholder
        if focused:
            line.append(shown[:1] or " ", style=on_caret)
            shown = shown[1:]
        line.append(shown, style=muted)
        return fit_text(line, inner, frame.ellipsis())
    if not focused:
        return fit_text(Text(text, style=value), inner, frame.ellipsis())
    start = _scroll_start(text, cursor, inner)
    visible = text[start:]
    line = fit_text(Text(visible, style=value), inner, frame.ellipsis())
    # The caret is styled after the cut: a span that reached the last kept cell
    # would otherwise be stretched over the ellipsis by ``fit_text``.
    offset = cursor - start
    if offset < _kept_chars(visible, inner, frame.ellipsis()):
        line.stylize(caret, offset, offset + 1)
    return line


def _scroll_start(text: str, cursor: int, inner: int) -> int:
    """Where the visible part of ``text`` starts so the caret shows and the view holds still.

    While the caret is in the last cells of the text the view is anchored to its
    end, with one cell kept free for a caret past the end (so the caret moves
    along the row instead of the text sliding under a caret stuck at the right
    edge); further left, the caret sits with some text before it and the cut
    with the ellipsis on its right.
    """
    if cursor < len(text) and cell_len(text) <= inner:
        return 0  # it all fits, nothing to scroll
    start, used = len(text), 1  # the caret's own cell is always kept free
    while start > 0 and used + cell_len(text[start - 1]) <= inner:
        start -= 1
        used += cell_len(text[start])
    if start <= cursor:
        return start
    width = cell_len(text[cursor : cursor + 1])
    lookback = max(0, min(inner // 2, inner - 1 - width))
    start = cursor
    while start > 0 and cell_len(text[start - 1 : cursor]) <= lookback:
        start -= 1
    return start


def _kept_chars(visible: str, inner: int, ellipsis: str) -> int:
    """How many leading characters of ``visible`` ``fit_text`` keeps (a caret may follow them)."""
    if cell_len(visible) <= inner:
        return len(visible) + 1
    kept, used = 0, 0
    while kept < len(visible) and used + cell_len(visible[kept]) <= inner - cell_len(ellipsis):
        used += cell_len(visible[kept])
        kept += 1
    return kept
