"""The workspace picker as a screen: the reducer is its update, the components draw it.

:func:`picker_screen` wraps a :class:`~untaped.picker.PickRequest` in a
:class:`~untaped.screen.core.Screen`. The model is the reducer's
:class:`~untaped.picker.state.PickerState` and ``update`` hands every key to
:func:`~untaped.picker.state.handle`, so the picker's logic stays in one place
and its tests carry over; this module adds only what the runtime needs around
it (the catalog refresh as a command, how the screen ends) and the view.

The view is built from the screen components (``Panes``, ``SearchList``,
``Tree``, ``TextInput``, ``Buttons``) given the state to draw, never to run:
they hold no focus or cursor of their own here. Every glyph, colour and border
comes from the theme through the frame.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace

from rich.align import Align
from rich.cells import cell_len
from rich.console import Group, RenderableType
from rich.text import Text

from untaped.picker import (
    PickCatalog,
    PickItem,
    PickRequest,
    PickResult,
)
from untaped.picker.state import (
    ALL,
    CREATE,
    PickerState,
    begin_refresh,
    candidates,
    completions,
    handle,
    initial_state,
    is_inherited,
    press,
    refresh_failed,
    result,
    row_index,
    rows,
    setting_for,
    setting_value,
    visible,
    with_catalog,
)
from untaped.screen.components.buttons import BOX_ROWS, Button, Buttons
from untaped.screen.components.choices import ListItem
from untaped.screen.components.draw import role_style, text_line, unboxed
from untaped.screen.components.inputs import TextInput
from untaped.screen.components.layout import Panes
from untaped.screen.components.lists import SearchList, Tree, TreeRow
from untaped.screen.core import (
    SHARED_KEYS,
    Activate,
    Back,
    Binding,
    Cancel,
    Cmd,
    CmdError,
    Frame,
    Help,
    Interrupt,
    Key,
    NextField,
    Paste,
    PrevField,
    Quit,
    Screen,
    Submit,
)
from untaped.screen.fit import fit_text
from untaped.screen.fuzzy import Ranked

__all__ = ["picker_screen"]

LIST_ROWS = 10
"""Most list rows the search pane shows; the right pane is sized to match it."""

_SEARCH_LINES = 2
"""The search pane's rows besides its list: the search line and the count line."""
_BOX_MIN_ROWS = LIST_ROWS + _SEARCH_LINES
"""The pane height from which the Create button keeps its box: as tall as the search pane."""
_EDITOR_BOX_MIN_ROWS = 18
"""The pane height from which the field being edited keeps its box and its candidates' rule."""
_ID_SEPARATOR = "\x00"
"""Joins an owner and a setting key into one tree row id (neither contains it)."""


@dataclass(frozen=True)
class _Loaded:
    """A refresh finished with this catalog."""

    catalog: PickCatalog


@dataclass(frozen=True)
class _Refresh:
    """ctrl-r: refresh now, whatever the cache says."""


# --- the screen ---------------------------------------------------------------


def picker_screen(request: PickRequest) -> Screen[PickerState, PickResult]:
    """The picker for ``request``; it quits with the :class:`PickResult` or is cancelled."""

    def init() -> tuple[PickerState, list[Cmd]]:
        state = initial_state(request)
        if request.refresh is None:
            return state, []
        return begin_refresh(state), [_refresh(request.refresh, force=False)]

    def update(state: PickerState, message: object) -> tuple[PickerState, list[Cmd]]:
        return _update(request, state, message)

    return Screen(
        init=init,
        update=update,
        view=_view,
        title=request.heading,
        command=request.terminal_command,
        alternative=request.terminal_alternative,
        keys=_KEYS,
        shared_labels=_SHARED_LABELS,
        layout="full",
    )


def _refresh(source: Callable[[bool], PickCatalog], *, force: bool) -> Cmd:
    """The catalog fetch as a command; an exception reaches ``update`` as a ``CmdError``."""

    def fetch() -> _Loaded:
        return _Loaded(source(force))

    return Cmd(fetch, name="refresh")


def _update(
    request: PickRequest, state: PickerState, message: object
) -> tuple[PickerState, list[Cmd]]:
    cmds: list[Cmd] = []
    match message:
        case Key(name):
            new = _key(state, name)
        case Paste(text):
            new = _paste(state, text)
        case _Loaded(catalog):
            new = with_catalog(state, catalog)
        case CmdError(error):
            new = refresh_failed(state, str(error) or type(error).__name__)
        case _Refresh():
            if request.refresh is None or state.refreshing:
                return (replace(state, error="") if state.error else state), []
            new = replace(begin_refresh(state), error="")  # a retry answers "refresh failed"
            cmds.append(_refresh(request.refresh, force=True))
        case Back() | Interrupt():
            # Esc and ctrl-c nothing else wanted: ask before discarding a selection.
            new = handle(state, "ctrl-c")
        case NextField() | PrevField() | Activate() | Submit() | Help():
            new = replace(state, error="") if state.error else state  # any key dismisses it
        case _:
            return state, []
    if state.outcome == "running":
        cmds.extend(_ending(new))
    return new, cmds


def _ending(state: PickerState) -> list[Cmd]:
    """The command that ends the screen once the reducer has decided how."""
    if state.outcome == "confirmed":
        return [Cmd.send(Quit(result(state)))]
    if state.outcome == "cancelled":
        return [Cmd.send(Cancel())]
    return []


def _paste(state: PickerState, text: str) -> PickerState:
    for char in text:
        if char.isprintable():
            state = handle(state, char)
    return state


def _key(state: PickerState, name: str) -> PickerState:
    if name == "ctrl-r" and state.request.refresh is not None:
        return state  # the screen's own binding, not a key the reducer knows
    new, used = press(state, name)
    if used or name not in SHARED_KEYS:
        return new  # a key the picker used, or one nobody else wants: the error is dismissed
    return state  # a shared key it has no use for: the SDK's message follows (and dismisses it)


# --- keys ----------------------------------------------------------------------


def _searching(state: PickerState) -> bool:
    return state.focus == "search"


def _in_list(state: PickerState) -> bool:
    return state.focus == "list"


def _on_item(state: PickerState) -> bool:
    owner, key = state.row
    return state.focus == "selected" and key is None and owner not in (ALL, CREATE)


def _on_choice(state: PickerState) -> bool:
    key = state.row[1]
    return (
        state.focus == "selected"
        and key is not None
        and bool(setting_for(state, key).choices)
        and state.editing is None
    )


def _can_refresh(state: PickerState) -> bool:
    return state.request.refresh is not None


#: The footer and help overlay entries. Tab, enter and ctrl-s are the SDK's shared keys, so
#: the overlay lists them; the keys below are the picker's own, most of them footer-only
#: because the reducer handles them.
_KEYS = (
    Binding("down", "browse", None, when=_searching),
    Binding(" ", "toggle", None, when=_in_list),
    Binding("/", "search", None, when=_in_list),
    Binding(" ", "remove", None, when=_on_item),
    Binding("left", "previous", None, when=_on_choice),
    Binding("right", "next", None, when=_on_choice),
    Binding("ctrl-r", "refresh", _Refresh(), when=_can_refresh),
)


def _tab_label(state: PickerState) -> str | None:
    """Tab moves between the panes, except where it means something else (completing, answering)."""
    return None if state.editing is not None or state.quitting else "pane"


def _enter_label(state: PickerState) -> str | None:
    """What enter does where it is worth saying: edit a text setting, press Create."""
    owner, key = state.row
    if state.focus != "selected" or state.editing is not None:
        return None
    if owner == CREATE:
        return "create"
    if key is not None and not setting_for(state, key).choices:
        return "edit"
    return None


def _esc_label(state: PickerState) -> str | None:
    """Esc clears a typed query before it goes back, like a ``SearchList``."""
    return "clear" if state.query and state.focus in ("search", "list") else None


#: What the shared keys do here, for the footer and the help overlay. They stay the SDK's
#: keys; the screen only says what they mean in the picker.
_SHARED_LABELS: Mapping[str, str | Callable[[PickerState], str | None]] = {
    "tab": _tab_label,
    "enter": _enter_label,
    "esc": _esc_label,
    "ctrl-s": "create",
}


# --- the view ------------------------------------------------------------------


def _view(state: PickerState, frame: Frame) -> RenderableType:
    panes = Panes(
        _SearchPane(state),
        _SelectedPane(state),
        focus=1 if state.focus == "selected" else 0,
        left_title="Search",
        right_title=f"Selected {len(state.selected)}",
        split=0.5,
    )
    inside = replace(frame, height=max(1, frame.height - 2))  # the header and the message line
    parts: list[RenderableType] = [_header(state, frame), panes.view(inside, focused=True)]
    if message := _message(state, frame):
        parts.append(message)
    return Group(*parts)


def _header(state: PickerState, frame: Frame) -> Text:
    """The heading, the title (a field when the request asks for one) and the live subtitle."""
    request = state.request
    line = Text()
    line.append(f" {frame.symbol('heading')} ", style=role_style(frame, "screen.accent"))
    line.append(request.heading, style=role_style(frame, "screen.accent"))
    if request.title_label or state.title:
        line.append("  ")
        if request.title_label:
            room = min(max(cell_len(state.title) + 2, cell_len(request.title_label) + 2, 12), 30)
            line.append_text(
                text_line(
                    frame,
                    room,
                    state.title,
                    len(state.title),
                    focused=state.focus == "title",
                    placeholder=request.title_label,
                )
            )
        else:
            line.append(state.title, style=role_style(frame, "screen.value"))
    if request.subtitle is not None:
        subtitle = request.subtitle(state.title, state.defaults)
        gap = frame.width - line.cell_len - cell_len(subtitle) - 1
        if gap >= 2:
            line.append(" " * gap)
            line.append(subtitle, style=role_style(frame, "screen.muted"))
    return fit_text(line, frame.width, frame.ellipsis())


def _message(state: PickerState, frame: Frame) -> Text | None:
    """The line under the panes: the discard question, or the last error."""
    if state.quitting:
        text = f"discard {len(state.selected)} selected? y/n"
    elif state.error:
        text = state.error
    else:
        return None
    return fit_text(
        Text(f" {text}", style=role_style(frame, "screen.error")), frame.width, frame.ellipsis()
    )


_listing: list[
    tuple[list[Ranked[PickItem]], tuple[ListItem, ...], tuple[Ranked[ListItem], ...]]
] = []
"""One slot: the list items (and the ranking of them) last built for the reducer's ranking."""


def _list_items(state: PickerState) -> tuple[tuple[ListItem, ...], tuple[Ranked[ListItem], ...]]:
    """The picker's items, and the reducer's ranking of them, as the search list draws them.

    The reducer ranks once; the list takes that ranking as it is. The same
    objects come back while the reducer's ranking is the same one, so a key
    press builds them once instead of once per frame.
    """
    ranking = visible(state)
    if _listing and _listing[0][0] is ranking:
        return _listing[0][1], _listing[0][2]
    built = {
        id(item): ListItem(
            item.id,
            item.label,
            detail=item.description,
            description=item.description,
            dimmed=item.dimmed,
        )
        for item in candidates(state)
    }
    items = tuple(built.values())
    entries = tuple(Ranked(built[id(match.item)], match.positions) for match in ranking)
    _listing[:] = [(ranking, items, entries)]
    return items, entries


@dataclass(frozen=True)
class _SearchPane:
    """The left pane: the search line and the matching items, from the state."""

    state: PickerState

    def __call__(self, frame: Frame, focused: bool) -> RenderableType:
        state = self.state
        notes = [state.note] if state.note else []
        if state.refreshing:
            notes.append(f"refreshing{frame.ellipsis()}")
        elif state.stale:
            notes.append("refresh failed")
        items, entries = _list_items(state)
        search = SearchList(
            "",
            items,
            entries=entries,
            window_rows=max(1, min(LIST_ROWS, frame.height - _SEARCH_LINES)),
            query=state.query,
            cursor=state.cursor,
            selected=frozenset(state.selected),
            multi=True,
            note=f" {frame.symbol('separator')} ".join(notes),
            caret=state.focus == "search",
            highlight=state.focus == "list",
        )
        return search.view(frame, focused=focused)


@dataclass(frozen=True)
class _SelectedPane:
    """The right pane: the owners and their settings as a tree, an editor, the Create button."""

    state: PickerState

    def __call__(self, frame: Frame, focused: bool) -> RenderableType:
        state = self.state
        inner = frame.width
        bare = unboxed(frame)
        on_create = state.row[0] == CREATE
        button = Buttons((Button("create", "Create", "primary"),))
        boxed = (
            frame.box() is not None
            and inner >= button.boxed_width
            and frame.height >= _BOX_MIN_ROWS
        )
        button_rows = BOX_ROWS if boxed else 1
        # The field being edited keeps its box while the pane has the rows for it.
        boxed_editor = frame.box() is not None and frame.height >= _EDITOR_BOX_MIN_ROWS
        editor_frame = frame if boxed_editor else bare
        editor = self._editor(editor_frame) if state.editing is not None else None
        editor_rows = 0
        if editor is not None:
            candidates = min(5, len(editor.matches or ())) if editor.completing else 0
            # The label line (a border when boxed), the value, the help; boxed adds the bottom
            # border and a rule over the candidates.
            editor_rows = 3 + candidates
            if boxed_editor:
                editor_rows += 1 + bool(candidates)
        # As tall as the search pane when the room allows: its list, search and count lines.
        wanted = LIST_ROWS + _SEARCH_LINES - 1 - button_rows
        window = max(3, min(wanted, frame.height - 1 - button_rows - editor_rows))
        tree = Tree(
            "",
            self._tree_rows(frame),
            expanded=frozenset({state.row[0]}),
            cursor=row_index(state),
            window_rows=window,
        )
        parts: list[RenderableType] = [tree.view(bare, focused=focused and not on_create)]
        shown = min(window, len(rows(state)) - 1)
        parts.extend(Text("") for _ in range(window - shown))
        if editor is not None:
            parts.append(editor.view(editor_frame, focused=True, width=inner))
        parts.append(Text(""))
        parts.append(self._button(button, frame, inner, boxed=boxed, focused=focused and on_create))
        return Group(*parts)

    def _tree_rows(self, frame: Frame) -> tuple[TreeRow, ...]:
        state = self.state
        settings = state.request.settings
        separator = f" {frame.symbol('separator')} "
        out: list[TreeRow] = []
        for owner in (ALL, *state.selected):
            out.append(
                TreeRow(
                    owner,
                    "all items" if owner == ALL else state.known[owner].label,
                    separator.join(_shown(state, owner, s.key, frame) for s in settings),
                    tuple(
                        TreeRow(
                            f"{owner}{_ID_SEPARATOR}{s.key}",
                            s.label,
                            _setting_text(state, owner, s.key, frame),
                        )
                        for s in settings
                    ),
                )
            )
        return tuple(out)

    def _editor(self, frame: Frame) -> TextInput:
        """The text setting being edited: its buffer, its candidates and how to leave."""
        state = self.state
        _owner, key = state.row
        assert key is not None  # editing starts on a setting row
        return TextInput(
            setting_for(state, key).label,
            state.editing or "",
            help=f"enter save {frame.symbol('separator')} esc cancel",
            # The reducer owns the candidates (and tab takes the first), so the input only
            # shows them: handed over with the text they were computed for.
            matches=tuple(c for c in completions(state) if c != state.editing),
            matched=state.editing or "",
        )

    def _button(
        self, buttons: Buttons, frame: Frame, inner: int, *, boxed: bool, focused: bool
    ) -> RenderableType:
        drawn = buttons.view(frame if boxed else unboxed(frame), focused=focused, width=inner)
        return Align.center(drawn, width=inner)


def _shown(state: PickerState, owner: str, key: str, frame: Frame) -> str:
    """The setting's effective value, the request's placeholder, or the dash."""
    value = setting_value(state, owner, key)
    if value:
        return value
    return setting_for(state, key).placeholder or frame.symbol("dash")


def _setting_text(state: PickerState, owner: str, key: str, frame: Frame) -> str:
    """A setting row's value: ``inherit (value)``, a choice between the cycle symbols, or text."""
    value = _shown(state, owner, key, frame)
    if is_inherited(state, owner, key):
        return f"{frame.symbol('separator')} inherit ({value})"
    if setting_for(state, key).choices:
        return f"{frame.symbol('cycle.left')} {value} {frame.symbol('cycle.right')}"
    return value
