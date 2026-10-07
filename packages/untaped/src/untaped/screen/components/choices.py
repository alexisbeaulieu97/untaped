"""The choice components: ``Check``, ``Select``, ``SingleList``, ``MultiList`` and ``Cycle``.

Each is a labelled box like the text components. The cursor of a list is only
the highlighted row (bright text on the theme's highlight fill, the one fill a
component draws); the chosen value of a single choice is marked by the theme's
``chosen`` symbol, a multiple choice by ``[`` ``checked`` ``]``, and a boolean
is a coloured ``on`` or ``off`` symbol with no text. A key a component does
not consume gives back the same object, so ``esc`` closes an open ``Select``
and otherwise goes on to Back, and ``enter`` opens a closed ``Select`` and
otherwise goes on to the form.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, replace
from typing import Any, Self

from rich.cells import cell_len
from rich.console import Group, RenderableType
from rich.style import Style
from rich.text import Text

from untaped.screen.components.box import field_box
from untaped.screen.components.draw import (
    DIM,
    divider,
    inner_width,
    option_row,
    role_style,
    window_start,
)
from untaped.screen.core import Cmd, Frame, Key
from untaped.stability import experimental

__all__ = ["Check", "Cycle", "ListItem", "MultiList", "Select", "SingleList"]

#: The most rows an open ``Select`` or a list shows at once.
MAX_ROWS = 8


@experimental
@dataclass(frozen=True)
class ListItem:
    """One choice: ``id`` is what a component's ``value`` holds, ``label`` what it shows.

    ``detail`` is shown right-aligned in the style of ``detail_role`` (a
    ``screen.*`` role); ``description`` is longer text a searching component
    matches on; ``dimmed`` draws the row muted for something unavailable.
    """

    id: str
    label: str
    detail: str = ""
    detail_role: str = "screen.muted"
    description: str = ""
    dimmed: bool = False


def clamped(cursor: int | None, count: int) -> int:
    return max(0, min(cursor or 0, count - 1)) if count else 0


def _index(items: Sequence[ListItem], item_id: str) -> int:
    return next((index for index, item in enumerate(items) if item.id == item_id), 0)


def moved_to(name: str, cursor: int, count: int) -> int | None:
    """The cursor after ``name`` (up, down, home, end), clamped; ``None`` for any other key."""
    match name:
        case "up":
            return max(cursor - 1, 0)
        case "down":
            return min(cursor + 1, count - 1)
        case "home":
            return 0
        case "end":
            return count - 1
    return None


def item_row(
    frame: Frame,
    inner: int,
    item: ListItem,
    *,
    cursor: bool,
    chosen: bool,
    lead: Sequence[tuple[str, Style]],
    marks: Collection[int] = (),
) -> Text:
    """One list row: ``lead`` cells, the label, the detail; highlighted when it is the cursor.

    ``marks`` are the label positions a search matched.
    """
    if cursor:
        label_style = role_style(frame, "screen.highlight")
        trail_style = label_style
    else:
        label_style = (
            role_style(frame, "screen.value", "screen.emphasis")
            if chosen
            else role_style(frame, "screen.muted") + (DIM if item.dimmed else Style.null())
        )
        trail_style = role_style(frame, item.detail_role)
    return option_row(
        frame,
        inner,
        lead=lead,
        label=item.label,
        label_style=label_style,
        trail=item.detail,
        trail_style=trail_style,
        base=role_style(frame, "screen.highlight") if cursor else None,
        marks=marks,
    )


def mark_lead(
    frame: Frame, item: ListItem, *, cursor: bool, chosen: bool, show: bool
) -> list[tuple[str, Style]]:
    """The cells before a single-choice label: the ``chosen`` symbol on the value, else blanks."""
    if not show:
        return []
    symbol = frame.symbol("chosen")
    layers = ("screen.highlight",) if cursor else ()
    edge = _edge_style(frame, cursor=cursor)
    if chosen:
        return [
            (symbol, role_style(frame, *layers, "screen.success", "screen.emphasis")),
            (" ", edge),
        ]
    return [(" " * cell_len(symbol), edge), (" ", edge)]


def _edge_style(frame: Frame, *, cursor: bool) -> Style:
    """The muted style of a row's markers and brackets; the bare highlight on the cursor row.

    Layering muted over the highlight would draw them in the fill's own colour in
    themes whose muted colour is the highlight's background.
    """
    return role_style(frame, "screen.highlight" if cursor else "screen.muted")


def check_lead(frame: Frame, *, on: bool, cursor: bool) -> list[tuple[str, Style]]:
    """The cells before a multiple-choice label: ``[``, ``checked`` or ``unchecked``, ``]``."""
    layers = ("screen.highlight",) if cursor else ()
    edge = _edge_style(frame, cursor=cursor)
    mark = (
        (
            frame.symbol("checked"),
            role_style(frame, *layers, "screen.success", "screen.emphasis"),
        )
        if on
        else (frame.symbol("unchecked"), edge)
    )
    return [("[", edge), mark, ("]", edge), (" ", edge)]


def rows_budget(frame: Frame) -> int:
    return max(3, min(MAX_ROWS, frame.height - 6))


def window_rows(
    frame: Frame,
    items: Sequence[ListItem],
    cursor: int,
    make_row: Callable[[int, ListItem], Text],
    size: int | None = None,
) -> list[Text]:
    """The rows of the window that keeps row ``cursor`` in view; a long list is not drawn whole.

    ``size`` is the window's height; by default it follows the frame (:func:`rows_budget`).
    """
    size = size or rows_budget(frame)
    start = window_start(len(items), cursor, size)
    return [make_row(index, items[index]) for index in range(start, min(start + size, len(items)))]


@experimental
@dataclass(frozen=True)
class Check:
    """A boolean in a labelled box: the ``on`` symbol in the success colour or ``off`` in error.

    No text says which; the symbol is the state. Space toggles it; enter is left
    to the form (it activates or submits).
    """

    label: str
    value: bool = False
    help: str = ""
    error: str = ""

    def with_error(self, text: str) -> Self:
        """This check showing ``text`` as its error (empty clears it)."""
        return replace(self, error=text)

    def validate(self) -> str:
        """Always fine: a boolean has no invalid value."""
        return ""

    def update(self, message: object) -> tuple[Self, list[Cmd]]:
        """Toggle on space; every other key, enter included, is left alone."""
        if isinstance(message, Key) and message.name == " ":
            return replace(self, value=not self.value, error=""), []
        return self, []

    def view(
        self, frame: Frame, *, focused: bool = False, width: int | None = None
    ) -> RenderableType:
        """The box holding the coloured symbol."""
        if self.value:
            symbol = Text(
                frame.symbol("on"), style=role_style(frame, "screen.success", "screen.emphasis")
            )
        else:
            symbol = Text(
                frame.symbol("off"), style=role_style(frame, "screen.error", "screen.emphasis")
            )
        return field_box(
            frame,
            self.label,
            symbol,
            focused=focused,
            error=self.error,
            help=self.help,
            width=width,
        )


@experimental
@dataclass(frozen=True)
class Select:
    """A single choice that is closed (the value and ``expand``) until enter opens it.

    Open, the box shows the value with ``collapse``, a rule and the choices:
    the ``chosen`` symbol marks the value and the highlighted row is the
    cursor. Up and down move, enter picks and closes, esc closes without
    picking; both keys are consumed only while open. ``value`` is the chosen
    item's ``id`` (empty for none). The list is drawn open only while focused.
    """

    label: str
    choices: tuple[ListItem, ...]
    value: str = ""
    open: bool = False
    cursor: int | None = None
    help: str = ""
    error: str = ""

    def __post_init__(self) -> None:
        start = _index(self.choices, self.value) if self.cursor is None else self.cursor
        object.__setattr__(self, "cursor", clamped(start, len(self.choices)))

    @property
    def shown(self) -> str:
        """The label of the chosen item (the raw value when no choice has that id)."""
        item = next((item for item in self.choices if item.id == self.value), None)
        return item.label if item is not None else self.value

    def with_error(self, text: str) -> Self:
        """This select showing ``text`` as its error (empty clears it)."""
        return replace(self, error=text)

    def validate(self) -> str:
        """Always fine: only a listed choice can be picked."""
        return ""

    def update(self, message: object) -> tuple[Self, list[Cmd]]:
        """Open on enter; while open move, pick on enter or close on esc."""
        if not isinstance(message, Key) or not self.choices:
            return self, []
        name = message.name
        if not self.open:
            if name == "enter":
                return replace(self, open=True, cursor=_index(self.choices, self.value)), []
            return self, []
        cursor = self.cursor or 0
        moved = moved_to(name, cursor, len(self.choices))
        if moved is not None:
            return (replace(self, cursor=moved) if moved != cursor else self), []
        if name == "enter":
            return replace(self, value=self.choices[cursor].id, open=False, error=""), []
        if name == "esc":
            return replace(self, open=False), []
        return self, []

    def view(
        self, frame: Frame, *, focused: bool = False, width: int | None = None
    ) -> RenderableType:
        """The box: closed, the value and ``expand``; open (and focused), the list below."""
        inner = inner_width(frame, width)
        opened = self.open and focused
        symbol = frame.symbol("collapse" if opened else "expand")
        value_style = role_style(frame, "screen.value")
        body: list[RenderableType] = [
            option_row(
                frame, inner, label=self.shown, label_style=value_style, trail=symbol,
                trail_style=value_style,
            )
        ]  # fmt: skip
        if opened:
            rule = divider(frame, inner)
            if rule is not None:
                body.append(rule)
            cursor = self.cursor or 0
            body.extend(
                window_rows(
                    frame,
                    self.choices,
                    cursor,
                    lambda index, item: item_row(
                        frame,
                        inner,
                        item,
                        cursor=index == cursor,
                        chosen=item.id == self.value,
                        lead=mark_lead(
                            frame,
                            item,
                            cursor=index == cursor,
                            chosen=item.id == self.value,
                            show=True,
                        ),
                    ),
                )
            )
        return field_box(
            frame, self.label, Group(*body), focused=focused, error=self.error, help=self.help,
            width=width,
        )  # fmt: skip


@experimental
@dataclass(frozen=True)
class SingleList:
    """An inline single choice: every item shows, the ``chosen`` symbol marks the value.

    The cursor is only the highlighted row; up and down move it, space or enter
    picks the item under it (enter on the value already chosen is left to the
    form). ``show_chosen=False`` leaves out the marker column. ``value`` is the
    chosen item's ``id`` (empty for none). ``window_rows`` is the window's height
    (by default it follows the frame, as a search list's does).
    """

    label: str
    items: tuple[ListItem, ...]
    value: str = ""
    cursor: int | None = None
    show_chosen: bool = True
    help: str = ""
    error: str = ""
    window_rows: int | None = None

    def __post_init__(self) -> None:
        start = _index(self.items, self.value) if self.cursor is None else self.cursor
        object.__setattr__(self, "cursor", clamped(start, len(self.items)))

    def with_error(self, text: str) -> Self:
        """This list showing ``text`` as its error (empty clears it)."""
        return replace(self, error=text)

    def validate(self) -> str:
        """Always fine: only a listed item can be picked."""
        return ""

    def update(self, message: object) -> tuple[Self, list[Cmd]]:
        """Move the cursor, or pick the item under it on space or enter."""
        if not isinstance(message, Key) or not self.items:
            return self, []
        cursor = self.cursor or 0
        moved = moved_to(message.name, cursor, len(self.items))
        if moved is not None:
            return (replace(self, cursor=moved) if moved != cursor else self), []
        if message.name in (" ", "enter") and self.items[cursor].id != self.value:
            return replace(self, value=self.items[cursor].id, error=""), []
        return self, []

    def view(
        self, frame: Frame, *, focused: bool = False, width: int | None = None
    ) -> RenderableType:
        """The box with one row per item (a window around the cursor when there are many)."""
        inner = inner_width(frame, width)
        cursor = self.cursor or 0
        rows = window_rows(
            frame,
            self.items,
            cursor,
            lambda index, item: item_row(
                frame,
                inner,
                item,
                cursor=focused and index == cursor,
                chosen=item.id == self.value,
                lead=mark_lead(
                    frame,
                    item,
                    cursor=focused and index == cursor,
                    chosen=item.id == self.value,
                    show=self.show_chosen,
                ),
            ),
            self.window_rows,
        )
        return field_box(
            frame, self.label, Group(*rows), focused=focused, error=self.error, help=self.help,
            width=width,
        )  # fmt: skip


@experimental
@dataclass(frozen=True)
class MultiList:
    """An inline multiple choice: ``[`` ``checked`` ``]`` rows with a highlighted cursor row.

    Up and down move the cursor and space toggles the item under it; enter is
    left to the form. ``value`` is the tuple of selected ``id`` s in item order.
    ``window_rows`` is the window's height (by default it follows the frame).
    """

    label: str
    items: tuple[ListItem, ...]
    selected: frozenset[str] = frozenset()
    cursor: int | None = None
    help: str = ""
    error: str = ""
    window_rows: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "cursor", clamped(self.cursor, len(self.items)))

    @property
    def value(self) -> tuple[str, ...]:
        """The selected ids, in the order of the items."""
        return tuple(item.id for item in self.items if item.id in self.selected)

    def with_error(self, text: str) -> Self:
        """This list showing ``text`` as its error (empty clears it)."""
        return replace(self, error=text)

    def validate(self) -> str:
        """Always fine: any selection, none included, is valid."""
        return ""

    def update(self, message: object) -> tuple[Self, list[Cmd]]:
        """Move the cursor, or toggle the item under it on space."""
        if not isinstance(message, Key) or not self.items:
            return self, []
        cursor = self.cursor or 0
        moved = moved_to(message.name, cursor, len(self.items))
        if moved is not None:
            return (replace(self, cursor=moved) if moved != cursor else self), []
        if message.name == " ":
            item_id = self.items[cursor].id
            return replace(self, selected=self.selected ^ {item_id}, error=""), []
        return self, []

    def view(
        self, frame: Frame, *, focused: bool = False, width: int | None = None
    ) -> RenderableType:
        """The box with one bracketed row per item."""
        inner = inner_width(frame, width)
        cursor = self.cursor or 0
        rows = window_rows(
            frame,
            self.items,
            cursor,
            lambda index, item: self._row(frame, inner, item, focused and index == cursor),
            self.window_rows,
        )
        return field_box(
            frame, self.label, Group(*rows), focused=focused, error=self.error, help=self.help,
            width=width,
        )  # fmt: skip

    def _row(self, frame: Frame, inner: int, item: ListItem, cursor: bool) -> Text:
        on = item.id in self.selected
        return item_row(
            frame,
            inner,
            item,
            cursor=cursor,
            chosen=on,
            lead=check_lead(frame, on=on, cursor=cursor),
        )


@experimental
@dataclass(frozen=True)
class Cycle:
    """A value changed in place with left and right, drawn between the ``cycle`` symbols.

    Left and right step through ``choices`` and wrap at the ends; the first
    change makes the value the field's own. ``inherited`` draws the value as
    coming from a default (``inherit (value)``) until then. ``choices`` are the
    values the component holds (strings, or anything else, such as ``None``,
    ``True`` and ``False``); it shows each as itself, or as the word at the same
    place in ``labels`` when those are given.
    """

    label: str
    choices: tuple[Any, ...]
    value: Any = ""
    inherited: bool = False
    help: str = ""
    error: str = ""
    labels: tuple[str, ...] = ()

    def _shown(self, value: Any) -> str:
        """The text of ``value``: its label when it is a choice that has one."""
        if value in self.choices:
            index = self.choices.index(value)
            if index < len(self.labels):
                return self.labels[index]
        return str(value)

    def with_error(self, text: str) -> Self:
        """This cycle showing ``text`` as its error (empty clears it)."""
        return replace(self, error=text)

    def validate(self) -> str:
        """Always fine: only a listed choice can be reached."""
        return ""

    def update(self, message: object) -> tuple[Self, list[Cmd]]:
        """Step to the previous or next choice on left or right, wrapping at the ends."""
        if (
            not isinstance(message, Key)
            or message.name not in ("left", "right")
            or not self.choices
        ):
            return self, []
        step = 1 if message.name == "right" else -1
        if self.value in self.choices:
            index = (self.choices.index(self.value) + step) % len(self.choices)
        else:
            index = 0 if step == 1 else len(self.choices) - 1
        return replace(self, value=self.choices[index], inherited=False, error=""), []

    def view(
        self, frame: Frame, *, focused: bool = False, width: int | None = None
    ) -> RenderableType:
        """The box: the value between the cycle symbols, or ``inherit (value)`` while inherited."""
        muted = role_style(frame, "screen.muted")
        line = Text()
        if self.inherited:
            line.append(
                f"{frame.symbol('separator')} inherit ({self._shown(self.value)})", style=muted
            )
        else:
            line.append(f"{frame.symbol('cycle.left')} ", style=muted)
            line.append(self._shown(self.value), style=role_style(frame, "screen.value"))
            line.append(f" {frame.symbol('cycle.right')}", style=muted)
        return field_box(
            frame, self.label, line, focused=focused, error=self.error, help=self.help, width=width
        )
