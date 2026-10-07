"""Scrolling and layout: ``Viewport``, ``window`` and the line-windowed stack of a form.

A view never draws a long list whole. :class:`Viewport` is the arithmetic of a
window over ``content_height`` rows (which rows show, how a key scrolls it,
how it follows a cursor); :func:`window` slices a sequence the same way, so a
list builds row text only for the rows that are on screen. :func:`windowed`
stacks blocks of any height and shows the window that keeps one of them in
sight, which is how a form taller than the terminal stays usable.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, replace

from rich.console import Console, ConsoleOptions, RenderableType, RenderResult
from rich.segment import Segment

from untaped.screen.components.draw import window_start
from untaped.screen.core import Key
from untaped.stability import experimental

__all__ = ["Viewport", "window", "windowed"]


@experimental
@dataclass(frozen=True)
class Viewport:
    """Which rows of ``content_height`` rows show in a window, and where it starts.

    ``offset`` is the first row asked for; it is clamped when read, so a window
    never runs past the end and a shrinking content needs no fix-up. A screen
    keeps one in its model to scroll text with no cursor (help, a log); lists
    use :meth:`centred` to follow their cursor.
    """

    content_height: int
    offset: int = 0

    def start(self, rows: int) -> int:
        """The first visible row of a window ``rows`` tall."""
        return max(0, min(self.offset, self.content_height - rows))

    def shown(self, rows: int) -> range:
        """The content rows a window ``rows`` tall shows."""
        first = self.start(rows)
        return range(first, min(first + max(rows, 0), self.content_height))

    def centred(self, cursor: int, rows: int) -> Viewport:
        """This viewport scrolled so row ``cursor`` is in the middle of the window.

        Near either end the window stops at the first or last row instead.
        """
        return self._at(window_start(self.content_height, cursor, rows))

    def around(self, first: int, stop: int, rows: int) -> Viewport:
        """This viewport scrolled to show rows ``first`` up to ``stop``.

        Nothing scrolls while the block fits in the first ``rows`` rows;
        otherwise the window is centred on it, or starts at its first row when
        it is taller than the window.
        """
        if stop <= rows:
            return self._at(0)
        if stop - first >= rows:
            return self._at(first)
        centred = first - (rows - (stop - first)) // 2
        return self._at(max(0, min(centred, self.content_height - rows)))

    def scrolled(self, by: int, rows: int) -> Viewport:
        """This viewport moved ``by`` rows (negative scrolls up), kept inside the content."""
        return self._at(max(0, min(self.start(rows) + by, self.content_height - rows)))

    def update(self, message: object, rows: int) -> Viewport:
        """Scroll on up, down, home and end; any other message gives back this same object."""
        if not isinstance(message, Key):
            return self
        match message.name:
            case "up":
                return self.scrolled(-1, rows)
            case "down":
                return self.scrolled(1, rows)
            case "home":
                return self._at(0)
            case "end":
                return self._at(max(0, self.content_height - rows))
        return self

    def _at(self, offset: int) -> Viewport:
        offset = max(offset, 0)
        return self if offset == self.offset else replace(self, offset=offset)


def window[T](items: Sequence[T], cursor: int, rows: int) -> tuple[int, Sequence[T]]:
    """The first index and the slice of ``items`` a window ``rows`` tall shows around ``cursor``.

    The cursor sits in the middle where there is room (the picker's rule). A
    view builds its row text for the slice only, never for the whole list.
    """
    shown = Viewport(len(items)).centred(cursor, rows).shown(rows)
    return shown.start, items[shown.start : shown.stop]


def windowed(
    blocks: Sequence[RenderableType], focus: int, rows: int, *, gap: int = 1
) -> RenderableType:
    """``blocks`` stacked ``gap`` lines apart, cut to ``rows`` lines around block ``focus``."""
    return _Windowed(tuple(blocks), focus, rows, gap)


@dataclass(frozen=True)
class _Windowed:
    """Blocks drawn one under the other, at most ``rows`` lines, keeping block ``focus`` in sight.

    Each block is rendered at the width it is given, so its height is what it
    really takes; the window then follows :meth:`Viewport.around`.
    """

    blocks: tuple[RenderableType, ...]
    focus: int
    rows: int
    gap: int = 1

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        width = options.max_width
        sized = options.update(width=width, height=None)
        lines: list[list[Segment]] = []
        spans: list[tuple[int, int]] = []
        for index, block in enumerate(self.blocks):
            if index:
                lines.extend([Segment(" " * width)] for _ in range(self.gap))
            first = len(lines)
            lines.extend(console.render_lines(block, sized, pad=True))
            spans.append((first, len(lines)))
        if not lines:
            return
        first, stop = spans[max(0, min(self.focus, len(spans) - 1))]
        viewport = Viewport(len(lines)).around(first, stop, self.rows)
        yield from _joined(lines[index] for index in viewport.shown(self.rows))


def _joined(lines: Iterable[list[Segment]]) -> Iterator[Segment]:
    for number, line in enumerate(lines):
        if number:
            yield Segment.line()
        yield from line
