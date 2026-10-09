"""The long lists: ``SearchList``, ``Tree`` and ``Tags``.

``SearchList`` is a labelled box holding a search line, a window of rows and
a count line. Typing edits the query without a prefix key, the rows are the
items that match it best first (dimmed ones last, as
:func:`~untaped.screen.fuzzy.rank` orders them) with the matched letters in
bold and underline, and only the rows in the window are built, so a list of
thousands draws as fast as a short one. ``Tree`` is rows that expand into
their children, windowed the same way. ``Tags`` is the chosen items as
removable badges with a search list to add more, for a choice among too many
items to show.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from functools import cached_property
from itertools import chain
from typing import Self

from rich.cells import cell_len
from rich.console import Group, RenderableType
from rich.text import Text

from untaped.screen.components.box import field_box
from untaped.screen.components.choices import (
    ListItem,
    check_lead,
    clamped,
    item_row,
    mark_lead,
    moved_to,
)
from untaped.screen.components.draw import (
    divider,
    inner_width,
    option_row,
    role_style,
    text_line,
    unboxed,
)
from untaped.screen.components.layout import window
from untaped.screen.components.text import EditBuffer
from untaped.screen.core import Cmd, Frame, Key, NextField, Paste, PrevField
from untaped.screen.fit import fit_text
from untaped.screen.fuzzy import Ranked, rank
from untaped.stability import experimental

__all__ = ["SearchList", "Tags", "Tree", "TreeRow"]

#: The most list rows a ``SearchList`` shows at once, and the fewest it is squeezed to.
MAX_ROWS = 10
MIN_ROWS = 3
#: The rows of a frame a search list's box, search line and count line take, and a tree's box.
_CHROME = 8
_TREE_CHROME = 5

type _Entry = Ranked[ListItem]


def default_rows(frame: Frame) -> int:
    """The rows a ``SearchList`` shows by default: up to :data:`MAX_ROWS`, fewer when short."""
    return max(MIN_ROWS, min(MAX_ROWS, frame.height - _CHROME))


#: The last few rankings, keyed by the query and the very ``items`` tuple (kept here, so an
#: identity check stays valid): a key press ranks once, not in ``update`` and again in ``view``.
_RANKINGS: list[tuple[str, tuple[ListItem, ...], list[_Entry]]] = []
_RANKINGS_KEPT = 8


def _ranked(query: str, items: tuple[ListItem, ...]) -> list[_Entry]:
    for cached_query, cached_items, ranked in _RANKINGS:
        if cached_query == query and cached_items is items:
            return ranked
    ranked = rank(query, items)
    _RANKINGS.insert(0, (query, items, ranked))
    del _RANKINGS[_RANKINGS_KEPT:]
    return ranked


@experimental
@dataclass(frozen=True)
class SearchList:
    """A search box over a list of :class:`ListItem` s, for lists too long to show whole.

    Typing, backspace, ``ctrl-u`` and ``ctrl-w`` edit the query; the rows are
    the items matching every word of it, best first, with the matched letters in
    bold and underline, and ``dimmed`` items last. Up, down, home and end move
    the cursor (the highlighted row), which goes back to the first row when the
    query changes. Enter, or space while the query is empty, toggles the row
    under the cursor in a ``multi`` list and picks it in a single one (enter on
    the item already picked is left to the form). Esc clears a query first and
    is left to the screen once it is empty. A space in a non-empty query is
    text, so a query of several words can be typed.

    ``selected`` holds the picked ids; ``value`` is them in item order (a tuple
    when ``multi``, the one id or ``""`` otherwise). ``note`` is shown after the
    counts. Only the window of rows around the cursor is built.

    ``caret`` and ``highlight`` choose which half of a focused list is drawn:
    the caret in the search line (typing) and the highlighted cursor row
    (browsing). Both are on by default; a screen whose focus is in one mode or
    the other turns the other off. They change only the view.

    ``window_rows`` is the window's height (by default it follows the frame: up to
    :data:`MAX_ROWS`, less in a short one). ``entries`` are the matches already
    ranked, best first, for a screen that ranks the items itself and wants one
    ranking, not two; the list then ranks nothing and ``query`` is only shown
    and edited.
    """

    label: str
    items: tuple[ListItem, ...]
    query: str = ""
    cursor: int = 0
    selected: frozenset[str] = frozenset()
    multi: bool = False
    note: str = ""
    placeholder: str = "type to filter"
    help: str = ""
    error: str = ""
    caret: bool = True
    highlight: bool = True
    window_rows: int | None = None
    entries: tuple[_Entry, ...] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "cursor", clamped(self.cursor, len(self.matches)))

    @property
    def matches(self) -> Sequence[_Entry]:
        """The items matching the query, best first, each with its highlighted positions."""
        if self.entries is not None:
            return self.entries
        return _ranked(self.query, self.items)

    @property
    def value(self) -> tuple[str, ...] | str:
        """The picked ids in item order; for a single list, the one id (``""`` for none)."""
        picked = tuple(item.id for item in self.items if item.id in self.selected)
        if self.multi:
            return picked
        return picked[0] if picked else ""

    def with_error(self, text: str) -> Self:
        """This list showing ``text`` as its error (empty clears it)."""
        return replace(self, error=text)

    def validate(self) -> str:
        """Always fine: any pick, none included, is valid."""
        return ""

    def update(self, message: object) -> tuple[Self, list[Cmd]]:
        """Edit the query, move the cursor, or toggle or pick the row under it."""
        match message:
            case Key(name):
                return self._key(name), []
            case Paste(text):
                return self._edited(EditBuffer(self.query, len(self.query)).paste(text)), []
        return self, []

    def _key(self, name: str) -> Self:
        matches = self.matches
        step = moved_to(name, self.cursor, len(matches)) if matches else None
        if step is not None:
            return replace(self, cursor=step) if step != self.cursor else self
        if name == "enter" or (name == " " and not self.query):
            return self._picked(matches)
        if name == "esc":
            return replace(self, query="", cursor=0) if self.query else self
        if name in ("backspace", "ctrl-u", "ctrl-w") or (len(name) == 1 and name.isprintable()):
            return self._edited(EditBuffer(self.query, len(self.query)).key(name))
        return self

    def _edited(self, edited: EditBuffer | None) -> Self:
        if edited is None or edited.text == self.query:
            return self
        return replace(self, query=edited.text, cursor=0, error="")

    def _picked(self, matches: Sequence[_Entry]) -> Self:
        if not matches:
            return self
        item_id = matches[self.cursor].item.id
        if self.multi:
            return replace(self, selected=self.selected ^ {item_id}, error="")
        if self.selected == {item_id}:
            return self
        return replace(self, selected=frozenset({item_id}), error="")

    def view(
        self, frame: Frame, *, focused: bool = False, width: int | None = None
    ) -> RenderableType:
        """The box: the search line, a window of rows around the cursor and the count line."""
        inner = inner_width(frame, width)
        matches = self.matches
        size = self.window_rows or default_rows(frame)
        first, shown = window(matches, self.cursor, size)
        body: list[RenderableType] = [
            text_line(
                frame,
                inner,
                self.query,
                len(self.query),
                focused=focused and self.caret,
                placeholder=self.placeholder,
            )
        ]
        if (rule := divider(frame, inner)) is not None:
            body.append(rule)
        rows = [
            self._row(
                frame, inner, entry, focused and self.highlight and first + offset == self.cursor
            )
            for offset, entry in enumerate(shown)
        ]
        if not matches:
            rows.append(Text("no matches", style=role_style(frame, "screen.muted")))
        # Hold the height steady while the query narrows the list, so nothing below it jumps.
        rows.extend(Text("") for _ in range(min(size, len(self.items)) - len(rows)))
        body.extend(rows)
        body.append(self._counts(frame, inner, len(matches)))
        return field_box(
            frame,
            self.label,
            Group(*body),
            focused=focused,
            error=self.error,
            help=self.help,
            width=width,
        )

    def _row(self, frame: Frame, inner: int, entry: _Entry, cursor: bool) -> Text:
        item = entry.item
        on = item.id in self.selected
        lead = (
            check_lead(frame, on=on, cursor=cursor)
            if self.multi
            else mark_lead(frame, item, cursor=cursor, chosen=on, show=True)
        )
        return item_row(
            frame, inner, item, cursor=cursor, chosen=on, lead=lead, marks=entry.positions
        )

    def _counts(self, frame: Frame, inner: int, matching: int) -> Text:
        """The muted count line, right-aligned: matches, picks (multi) and the note."""
        parts = [f"{matching} of {len(self.items)}" if self.query else str(len(self.items))]
        if self.multi and self.selected:
            parts.append(f"{len(self.selected)} selected")
        if self.note:
            parts.append(self.note)
        counts = f" {frame.symbol('separator')} ".join(parts)
        line = Text(" " * max(0, inner - cell_len(counts)) + counts)
        line.stylize(role_style(frame, "screen.muted"))
        return fit_text(line, inner, frame.ellipsis())


@experimental
@dataclass(frozen=True)
class TreeRow:
    """One row of a :class:`Tree`: ``id`` is unique in the tree, ``summary`` is shown right-aligned.

    A row with ``children`` can be expanded; one without is a leaf.
    """

    id: str
    label: str
    summary: str = ""
    children: tuple[TreeRow, ...] = ()


@dataclass(frozen=True)
class _Shown:
    """A row on screen: how deep it sits and where its parent row is in the shown list."""

    row: TreeRow
    depth: int
    parent: int | None


def _shown(rows: tuple[TreeRow, ...], expanded: frozenset[str]) -> list[_Shown]:
    """The rows that are visible: every top row and the children of each expanded row, in order."""
    shown: list[_Shown] = []

    def walk(level: tuple[TreeRow, ...], depth: int, parent: int | None) -> None:
        for row in level:
            index = len(shown)
            shown.append(_Shown(row, depth, parent))
            if row.children and row.id in expanded:
                walk(row.children, depth + 1, index)

    walk(rows, 0, None)
    return shown


@experimental
@dataclass(frozen=True)
class Tree:
    """Rows that expand into their children, in a labelled box; only a window of them is built.

    ``expanded`` holds the ids of the open rows and ``cursor`` indexes the
    visible rows. Up, down, home and end move; right opens a closed row (and
    steps into an open one), left closes an open row (and steps out to the
    parent of a closed one), enter toggles a row that has children. A key a row
    cannot use, such as right on a leaf, is left to the parent, so a screen can
    give left and right another meaning there. ``value`` is the id of the row
    under the cursor (``""`` for an empty tree). ``window_rows`` is the number
    of rows shown at once (by default it follows the frame, as a
    :class:`SearchList`'s does).
    """

    label: str
    rows: tuple[TreeRow, ...]
    expanded: frozenset[str] = frozenset()
    cursor: int = 0
    help: str = ""
    error: str = ""
    window_rows: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "cursor", clamped(self.cursor, len(self._on_screen)))

    @cached_property
    def _on_screen(self) -> list[_Shown]:
        """The visible rows, walked once per tree (``update`` makes a new tree for any change)."""
        return _shown(self.rows, self.expanded)

    @property
    def value(self) -> str:
        """The id of the row under the cursor (empty when the tree is empty)."""
        visible = self._on_screen
        return visible[self.cursor].row.id if visible else ""

    def with_error(self, text: str) -> Self:
        """This tree showing ``text`` as its error (empty clears it)."""
        return replace(self, error=text)

    def validate(self) -> str:
        """Always fine: a tree holds no input."""
        return ""

    def update(self, message: object) -> tuple[Self, list[Cmd]]:
        """Move the cursor, expand or collapse the row under it, or step to its parent."""
        if not isinstance(message, Key):
            return self, []
        visible = self._on_screen
        if not visible:
            return self, []
        step = moved_to(message.name, self.cursor, len(visible))
        if step is not None:
            return (replace(self, cursor=step) if step != self.cursor else self), []
        current = visible[self.cursor]
        row, opened = current.row, current.row.id in self.expanded
        match message.name:
            case "right" if row.children:
                if opened:
                    return replace(self, cursor=self.cursor + 1), []
                return replace(self, expanded=self.expanded | {row.id}), []
            case "left" if row.children and opened:
                return replace(self, expanded=self.expanded - {row.id}), []
            case "left" if current.parent is not None:
                return replace(self, cursor=current.parent), []
            case "enter" if row.children:
                return replace(self, expanded=self.expanded ^ {row.id}), []
        return self, []

    def view(
        self, frame: Frame, *, focused: bool = False, width: int | None = None
    ) -> RenderableType:
        """The box: one line per visible row, indented by depth, in a window around the cursor."""
        inner = inner_width(frame, width)
        size = self.window_rows or max(MIN_ROWS, min(MAX_ROWS, frame.height - _TREE_CHROME))
        first, shown = window(self._on_screen, self.cursor, size)
        rows = [
            self._row(frame, inner, entry, focused and first + offset == self.cursor)
            for offset, entry in enumerate(shown)
        ]
        if not rows:
            rows.append(Text(""))
        return field_box(
            frame,
            self.label,
            Group(*rows),
            focused=focused,
            error=self.error,
            help=self.help,
            width=width,
        )

    def _row(self, frame: Frame, inner: int, entry: _Shown, cursor: bool) -> Text:
        row = entry.row
        layers = ("screen.highlight",) if cursor else ()
        edge = role_style(frame, *layers, "screen.muted")
        opened = row.id in self.expanded
        if row.children:
            marker = frame.symbol("tree.open" if opened else "tree.closed")
        else:
            marker = " " * cell_len(frame.symbol("tree.open"))
        label_style = role_style(frame, "screen.highlight" if cursor else "screen.value")
        return option_row(
            frame,
            inner,
            lead=[(" " * (2 * entry.depth), edge), (marker, edge), (" ", edge)],
            label=row.label,
            label_style=label_style,
            trail=row.summary,
            trail_style=label_style if cursor else role_style(frame, "screen.muted"),
            base=role_style(frame, "screen.highlight") if cursor else None,
        )


#: The most lines of badges a ``Tags`` shows; more are cut with the ellipsis.
MAX_BADGE_LINES = 3


@experimental
@dataclass(frozen=True)
class Tags:
    """The chosen items as removable badges in a labelled box, with a search list to add more.

    ``selected`` holds the chosen ids in the order they were added; ``value`` is
    that tuple. Backspace or delete removes the last badge; enter opens the menu
    (a single :class:`SearchList` of the items not chosen yet) while the field
    is focused, where a pick adds the badge and closes the menu, and esc clears
    its query and then closes it. Esc, enter and the editing keys are left to the
    screen whenever the field has no use for them (a closed menu and no badge
    for esc; no item left to add for enter). While the menu is open enter is
    always its own, even with no row to pick, and tab or shift-tab close it and
    then move focus on.
    """

    label: str
    items: tuple[ListItem, ...]
    selected: tuple[str, ...] = ()
    menu_open: bool = False
    menu: SearchList | None = field(default=None, repr=False)
    help: str = ""
    error: str = ""

    def __post_init__(self) -> None:
        if not self.menu_open:
            object.__setattr__(self, "menu", None)
        elif self.menu is None:
            object.__setattr__(self, "menu", self._fresh_menu())

    @property
    def value(self) -> tuple[str, ...]:
        """The chosen ids, in the order they were added."""
        return self.selected

    def _available(self) -> tuple[ListItem, ...]:
        return tuple(item for item in self.items if item.id not in self.selected)

    def _fresh_menu(self) -> SearchList:
        return SearchList("", self._available())

    def with_error(self, text: str) -> Self:
        """This field showing ``text`` as its error (empty clears it)."""
        return replace(self, error=text)

    def validate(self) -> str:
        """Always fine: any choice, none included, is valid."""
        return ""

    def update(self, message: object) -> tuple[Self, list[Cmd]]:
        """Open the menu, remove the last badge, or (with the menu open) drive the menu."""
        if self.menu is not None:
            return self._in_menu(self.menu, message)
        if not isinstance(message, Key):
            return self, []
        if message.name == "enter" and self._available():
            return replace(self, menu_open=True, menu=self._fresh_menu()), []
        if message.name in ("backspace", "delete") and self.selected:
            return replace(self, selected=self.selected[:-1], error=""), []
        return self, []

    def _in_menu(self, menu: SearchList, message: object) -> tuple[Self, list[Cmd]]:
        if isinstance(message, NextField | PrevField):
            # Close the menu, then hand the move on so the form moves focus off a closed field.
            return replace(self, menu_open=False, menu=None), [Cmd.send(message)]
        updated, _ = menu.update(message)
        if updated is not menu:
            if picked := updated.value:
                return (
                    replace(
                        self,
                        selected=(*self.selected, str(picked)),
                        menu_open=False,
                        menu=None,
                        error="",
                    ),
                    [],
                )
            return replace(self, menu=updated), []
        if isinstance(message, Key):
            if message.name == "esc":
                return replace(self, menu_open=False, menu=None), []
            if message.name == "enter":
                # An open menu keeps enter even with no row to pick, so a form never submits.
                return replace(self, menu=menu), []
        return self, []

    def view(
        self, frame: Frame, *, focused: bool = False, width: int | None = None
    ) -> RenderableType:
        """The box: badges and the add hint, and while open and focused the menu under a rule."""
        inner = inner_width(frame, width)
        body: list[RenderableType] = [*self._badges(frame, inner, focused)]
        if self.menu is not None and focused:
            if (rule := divider(frame, inner)) is not None:
                body.append(rule)
            body.append(self.menu.view(unboxed(frame), focused=True, width=inner))
        return field_box(
            frame,
            self.label,
            Group(*body),
            focused=focused,
            error=self.error,
            help=self.help,
            width=width,
        )

    def _badges(self, frame: Frame, inner: int, focused: bool) -> list[Text]:
        """The badges, then the add hint, wrapped to ``inner`` cells and cut after a few lines."""
        chosen = set(self.selected)
        labels = {item.id: item.label for item in self.items if item.id in chosen}
        cells = (
            Text.assemble(
                (
                    labels.get(item_id, item_id),
                    role_style(frame, "screen.value", "screen.emphasis"),
                ),
                " ",
                (frame.symbol("tag.remove"), role_style(frame, "screen.muted")),
            )
            for item_id in self.selected
        )
        hint = role_style(frame, "screen.accent" if focused else "screen.muted")
        add = Text(f"{frame.symbol('tag.add')} add", style=hint)
        lines = [Text()]
        for cell in chain(cells, [add]):
            if len(lines) > MAX_BADGE_LINES:
                break  # the rest would be cut anyway, so build no more badges
            cell = fit_text(cell, min(cell.cell_len, inner), frame.ellipsis())
            if lines[-1].cell_len and lines[-1].cell_len + 2 + cell.cell_len > inner:
                lines.append(Text())
            if lines[-1].cell_len:
                lines[-1].append("  ")
            lines[-1].append_text(cell)
        if len(lines) > MAX_BADGE_LINES:
            lines = lines[:MAX_BADGE_LINES]
            lines[-1].append(f" {frame.ellipsis()}", style=role_style(frame, "screen.muted"))
            lines[-1] = fit_text(lines[-1], inner, frame.ellipsis())
        return lines
