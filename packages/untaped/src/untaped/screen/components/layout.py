"""Scrolling and layout: ``Viewport``, ``window``, the line-windowed stack and ``Panes``.

A view never draws a long list whole. :class:`Viewport` is the arithmetic of a
window over ``content_height`` rows (which rows show, how a key scrolls it,
how it follows a cursor); :func:`window` slices a sequence the same way, so a
list builds row text only for the rows that are on screen. :func:`windowed`
stacks blocks of any height and shows the window that keeps one of them in
sight, which is how a form taller than the terminal stays usable. :class:`Panes`
puts two components in bordered panes side by side, or stacked on a narrow
terminal, with focus moving between them.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, replace
from typing import Self, cast

from rich.console import Console, ConsoleOptions, Group, RenderableType, RenderResult
from rich.panel import Panel
from rich.segment import Segment
from rich.text import Text

from untaped.screen.components.draw import role_style, unboxed, window_start
from untaped.screen.components.fields import Field
from untaped.screen.core import Cmd, Frame, Key, NextField, PrevField
from untaped.stability import experimental

__all__ = ["WIDE", "Drawing", "Panes", "Viewport", "window", "windowed"]

#: The terminal width from which ``Panes`` sit side by side; narrower, they stack.
WIDE = 100
#: The narrowest a pane is made side by side, and the fewest rows one gets when stacked.
MIN_PANE_WIDTH = 12
MIN_PANE_ROWS = 3


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


type Drawing = Callable[[Frame, bool], RenderableType]
"""A pane's body drawn from a model the screen owns: ``draw(frame, focused)``.

``frame`` is the room the pane's body has (its inner width and the rows it was
given). A pane built from one takes no input, holds no value and has no error;
the screen keeps the state and routes the keys.
"""


@dataclass(frozen=True)
class _Drawn:
    """A :data:`Drawing` as the component a pane holds: it draws and answers nothing else."""

    draw: Drawing
    value: object = ""
    error: str = ""

    def update(self, message: object) -> tuple[Self, list[Cmd]]:
        return self, []

    def with_error(self, text: str) -> Self:
        return self

    def validate(self) -> str:
        return ""

    def view(
        self, frame: Frame, *, focused: bool = False, width: int | None = None
    ) -> RenderableType:
        return self.draw(frame, focused)


def _field(child: Field | Drawing) -> Field:
    """The component a pane holds: ``child`` itself, or a drawing wrapped as one."""
    if hasattr(child, "update"):
        return cast(Field, child)
    return _Drawn(child)


@experimental
@dataclass(frozen=True)
class Panes:
    """Two components in bordered panes: side by side from :data:`WIDE` columns, stacked below.

    ``left`` and ``right`` are any components (a list, a :class:`Form`) or, for a
    pane that only draws a model it does not own, a :data:`Drawing`; ``focus``
    is ``0`` for the left pane and ``1`` for the right. Messages go to the focused
    one, and a ``NextField`` or ``PrevField`` it did not use (a form at its last or
    first field, a list, which has no use for them) moves focus to the other pane.
    The focused pane has the ``screen.focus`` border and its title is bright.
    ``split`` is the left pane's share of the width side by side.

    A pane's component is drawn without a box of its own when ``left_bare`` or
    ``right_bare`` is set (the default for the left, a list the pane's border
    already frames), so its label is better left empty; a form on the right keeps
    its boxed fields. ``value`` maps ``"left"`` and ``"right"`` to the components'
    values.
    """

    left: Field | Drawing
    right: Field | Drawing
    focus: int = 0
    left_title: str = ""
    right_title: str = ""
    split: float = 0.35
    left_bare: bool = True
    right_bare: bool = False
    error: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "focus", 0 if self.focus <= 0 else 1)

    @property
    def value(self) -> dict[str, object]:
        """``{"left": ..., "right": ...}``: what each pane's component holds."""
        return {"left": _field(self.left).value, "right": _field(self.right).value}

    def with_error(self, text: str) -> Self:
        """These panes showing ``text`` under them (empty clears it)."""
        return replace(self, error=text)

    def validate(self) -> str:
        """The first error of the two components (empty when both are fine); sets nothing."""
        return _field(self.left).validate() or _field(self.right).validate()

    def update(self, message: object) -> tuple[Self, list[Cmd]]:
        """Give ``message`` to the focused pane; focus moves to the other when it passed on tab."""
        child = _field(self.left if self.focus == 0 else self.right)
        updated, cmds = child.update(message)
        if updated is not child or cmds:
            if self.focus == 0:
                return replace(self, left=updated), list(cmds)
            return replace(self, right=updated), list(cmds)
        if isinstance(message, NextField | PrevField):
            return replace(self, focus=1 - self.focus), []
        return self, []

    def view(
        self, frame: Frame, *, focused: bool = False, width: int | None = None
    ) -> RenderableType:
        """The panes: equally tall side by side, or stacked (the focused one taller) when narrow."""
        total = width or frame.width
        side = total >= WIDE
        boxed = frame.box() is not None
        gap = 4 if boxed else 2  # the cells a border (and its padding) or a gutter takes
        chrome = 2 if boxed else 1  # the rows a border or a title line takes
        if side:
            left_width = max(MIN_PANE_WIDTH, min(round(total * self.split), total - MIN_PANE_WIDTH))
            widths = (left_width, total - left_width)
            rows = (max(MIN_PANE_ROWS, frame.height - chrome),) * 2
        else:
            widths = (total, total)
            spare = max(2 * MIN_PANE_ROWS, frame.height - 2 * chrome - (0 if boxed else 1))
            other = max(MIN_PANE_ROWS, spare // 3)
            rows = (spare - other, other) if self.focus == 0 else (other, spare - other)
        panes = tuple(
            self._pane(
                frame, index, widths[index], max(1, widths[index] - gap), rows[index], focused
            )
            for index in (0, 1)
        )
        shown: RenderableType = _PanesRender(frame, panes, side)
        if self.error:
            shown = Group(shown, Text(self.error, style=role_style(frame, "screen.error")))
        return shown

    def _pane(
        self, frame: Frame, index: int, width: int, inner: int, rows: int, focused: bool
    ) -> _Pane:
        on = focused and index == self.focus
        bare = self.left_bare if index == 0 else self.right_bare
        child = _field(self.left if index == 0 else self.right)
        child_frame = replace(frame, width=inner, height=rows)
        body = child.view(unboxed(child_frame) if bare else child_frame, focused=on, width=inner)
        title = self.left_title if index == 0 else self.right_title
        return _Pane(title, body, width, inner, on)


@dataclass(frozen=True)
class _Pane:
    """One pane ready to draw: its title and body, its cells (border included) and its focus."""

    title: str
    body: RenderableType
    width: int
    inner: int
    focused: bool


@dataclass(frozen=True)
class _Lines:
    """Lines already rendered, drawn again as they are (so a pane can be sized to its body)."""

    lines: tuple[list[Segment], ...]

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        yield from _joined(self.lines)


@dataclass(frozen=True)
class _PanesRender:
    """The panes of a :class:`Panes`, measured when drawn: equal height side by side."""

    frame: Frame
    panes: tuple[_Pane, ...]
    side: bool

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        bodies = [
            console.render_lines(pane.body, options.update(width=pane.inner, height=None), pad=True)
            for pane in self.panes
        ]
        height = max((len(lines) for lines in bodies), default=0)
        blocks = [
            self._block(console, options, pane, lines, height if self.side else len(lines))
            for pane, lines in zip(self.panes, bodies, strict=True)
        ]
        if self.side:
            yield from _joined(
                [segment for block in blocks for segment in block[row]]
                for row in range(len(blocks[0]))
            )
            yield Segment.line()  # end the last row, so whatever follows starts on its own line
            return
        gap = [] if self.frame.box() is not None else [[Segment(" ")]]
        stacked = [*blocks[0], *gap, *blocks[1]]
        yield from _joined(stacked)
        yield Segment.line()

    def _block(
        self,
        console: Console,
        options: ConsoleOptions,
        pane: _Pane,
        body: list[list[Segment]],
        rows: int,
    ) -> list[list[Segment]]:
        """The pane as lines ``pane.width`` cells wide: bordered, or a title line and a gutter."""
        frame = self.frame
        title_style = role_style(frame, "screen.accent" if pane.focused else "screen.value")
        sized = options.update(width=pane.width, height=None)
        if (outline := frame.box()) is not None:
            panel = Panel(
                _Lines(tuple(body)),
                title=Text(pane.title, style=title_style) if pane.title else None,
                title_align="left",
                box=outline,
                border_style=role_style(frame, "screen.focus" if pane.focused else "screen.border"),
                width=pane.width,
                height=rows + 2,
                padding=(0, 1),
            )
            return console.render_lines(panel, sized, pad=True)
        title = console.render_lines(
            Text(pane.title, style=title_style),
            options.update(width=pane.inner, height=None),
            pad=True,
        )
        blank = [Segment(" " * pane.inner)]
        padded = [*body, *([blank] * (rows - len(body)))]
        gutter = Segment(" " * (pane.width - pane.inner))
        return [[*line, gutter] for line in (*title, *padded)]
