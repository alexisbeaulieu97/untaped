"""Fitting one line of text to a width, with the theme's ellipsis token.

Views draw rows of exactly the width they were given; this is the one place
that truncates and pads, counting terminal cells (``cell_len``) rather than
characters so wide glyphs line up.
"""

from __future__ import annotations

from rich.cells import cell_len, set_cell_size
from rich.text import Span, Text

__all__ = ["fit_text"]


def fit_text(text: Text, width: int, ellipsis: str) -> Text:
    """``text`` cut or padded to exactly ``width`` cells; a cut ends in ``ellipsis``.

    The result is a copy, so ``text`` is never changed. Text that fits is padded
    with spaces. Longer text keeps ``width - cell_len(ellipsis)`` cells and gets
    ``ellipsis`` appended, in the style of the last kept cell. When the token
    alone is wider than ``width`` it is cropped to ``width`` cells; a ``width``
    of zero or less gives an empty text. Meant for a single line.
    """
    if width <= 0:
        return Text()
    result = text.copy()
    if result.cell_len <= width:
        return _padded(result, width)
    token_cells = cell_len(ellipsis)
    if token_cells >= width:
        return _padded(Text(set_cell_size(ellipsis, width), style=text.style), width)
    result.truncate(width - token_cells, overflow="crop")
    kept = len(result.plain)
    result.append(ellipsis)
    # ``append`` leaves the token unstyled; extend the spans that reach the cut
    # so a highlighted or coloured row keeps its style through the ellipsis.
    result.spans[:] = [
        Span(span.start, kept + len(ellipsis), span.style)
        if kept > 0 and span.end == kept and span.start < kept
        else span
        for span in result.spans
    ]
    return _padded(result, width)


def _padded(text: Text, width: int) -> Text:
    missing = width - text.cell_len
    if missing > 0:
        text.append(" " * missing)
    return text
