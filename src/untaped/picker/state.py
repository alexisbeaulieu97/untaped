"""Pure state machine behind the picker: one key name in, a new frozen state out.

Nothing here touches a terminal, so every interaction is unit-testable. The
prompt_toolkit front end (:mod:`untaped.picker.app`) maps real keys to the
names in :data:`KEY_NAMES` (or one printable character) and calls
:func:`handle`.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Literal

from untaped.picker import (
    PickCatalog,
    Picked,
    PickItem,
    PickRequest,
    PickResult,
    PickSetting,
)
from untaped.picker.fuzzy import Ranked, rank

Focus = Literal["title", "search", "list", "selected"]
Outcome = Literal["running", "confirmed", "cancelled"]

ALL = "\x00all"
"""Row owner for the all-items defaults row."""
CREATE = "\x00create"
"""Row owner for the [ Create ] button."""

Row = tuple[str, str | None]
"""``(owner, setting key)``; a ``None`` key is the owner's header row."""

KEY_NAMES = frozenset(
    {
        "up", "down", "left", "right", "tab", "enter", "esc",
        "backspace", "delete", "ctrl-u", "ctrl-w", "ctrl-s", "ctrl-c",
    }
)  # fmt: skip


@dataclass(frozen=True)
class PickerState:
    """Everything on screen; replaced, never mutated."""

    request: PickRequest
    items: tuple[PickItem, ...]
    note: str
    title: str
    focus: Focus
    defaults: Mapping[str, str]
    known: Mapping[str, PickItem]
    query: str = ""
    cursor: int = 0
    selected: tuple[str, ...] = ()
    overrides: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    row: Row = (ALL, None)
    editing: str | None = None
    quitting: bool = False
    refreshing: bool = False
    stale: bool = False
    error: str = ""
    outcome: Outcome = "running"


def initial_state(request: PickRequest) -> PickerState:
    """The state the picker opens in."""
    needs_title = bool(request.title_label) and not request.title
    return PickerState(
        request=request,
        items=request.catalog.items,
        note=request.catalog.note,
        title=request.title,
        focus="title" if needs_title else "search",
        defaults={setting.key: setting.default for setting in request.settings},
        known={item.id: item for item in request.catalog.items},
    )


# --- derived views ---------------------------------------------------------


def visible(state: PickerState) -> list[Ranked]:
    """The left-pane rows for the current query, best match first."""
    items = state.items
    query = state.query.strip()
    if state.request.adhoc is not None and query:
        extra = state.request.adhoc(query)
        if extra is not None and all(item.id != extra.id for item in items):
            items = (extra, *items)
    return rank(state.query, items)


def rows(state: PickerState) -> list[Row]:
    """The right-pane rows; only the highlighted owner shows its setting rows."""
    expanded = state.row[0]
    out: list[Row] = []
    for owner in (ALL, *state.selected):
        out.append((owner, None))
        if owner == expanded:
            out.extend((owner, setting.key) for setting in state.request.settings)
    out.append((CREATE, None))
    return out


def setting_value(state: PickerState, owner: str, key: str) -> str:
    """The effective value: the owner's override, else the default."""
    own = state.overrides.get(owner, {})
    return own[key] if owner != ALL and key in own else state.defaults[key]


def is_inherited(state: PickerState, owner: str, key: str) -> bool:
    """Whether an item row shows the default rather than its own value."""
    return owner != ALL and key not in state.overrides.get(owner, {})


def completions(state: PickerState) -> list[str]:
    """Candidates for the text field being edited that start with the buffer."""
    owner, key = state.row
    if state.editing is None or key is None:
        return []
    setting = _setting(state, key)
    if setting.complete is None:
        return []
    item_id = None if owner == ALL else owner
    return [value for value in setting.complete(item_id) if value.startswith(state.editing)]


def result(state: PickerState) -> PickResult:
    """The confirmed selection with every setting resolved."""
    keys = [setting.key for setting in state.request.settings]
    picks = tuple(
        Picked(
            item=state.known[item_id],
            settings={key: setting_value(state, item_id, key) for key in keys},
        )
        for item_id in state.selected
    )
    return PickResult(title=state.title.strip(), defaults=dict(state.defaults), picks=picks)


# --- catalog refresh -------------------------------------------------------


def begin_refresh(state: PickerState) -> PickerState:
    """Mark a background refresh as running."""
    return replace(state, refreshing=True)


def with_catalog(state: PickerState, catalog: PickCatalog) -> PickerState:
    """Swap in a refreshed catalog; selected items stay selected."""
    known = {**state.known, **{item.id: item for item in catalog.items}}
    swapped = replace(
        state, items=catalog.items, note=catalog.note, known=known, refreshing=False, stale=False
    )
    return replace(swapped, cursor=min(state.cursor, max(0, len(visible(swapped)) - 1)))


def refresh_failed(state: PickerState, message: str) -> PickerState:
    """Keep the current catalog, say why the refresh failed, and mark it stale."""
    return replace(state, refreshing=False, stale=True, error=f"refresh failed: {message}")


# --- key handling ----------------------------------------------------------


def handle(state: PickerState, key: str) -> PickerState:
    """Apply one key: a name from :data:`KEY_NAMES` or one printable character."""
    if state.error:
        state = replace(state, error="")
    if state.quitting:
        return _answer_quit(state, key)
    if state.editing is not None:
        return _edit_key(state, key)
    action = _GLOBAL.get(key) or _BY_FOCUS[state.focus].get(key)
    if action is not None:
        return action(state)
    if len(key) == 1 and key.isprintable():
        return _type(state, key)
    return state


def _edit_text(text: str, key: str) -> str | None:
    if key == "backspace":
        return text[:-1]
    if key == "ctrl-u":
        return ""
    if key == "ctrl-w":
        return re.sub(r"\S+\s*$", "", text)
    return None


def _type(state: PickerState, char: str) -> PickerState:
    if state.focus == "title":
        return replace(state, title=state.title + char)
    if state.focus in ("search", "list"):
        return replace(state, focus="search", query=state.query + char, cursor=0)
    return state


def _edit_title(key: str) -> Callable[[PickerState], PickerState]:
    def apply(state: PickerState) -> PickerState:
        return replace(state, title=_edit_text(state.title, key) or "")

    return apply


def _edit_query(key: str) -> Callable[[PickerState], PickerState]:
    def apply(state: PickerState) -> PickerState:
        return replace(state, focus="search", query=_edit_text(state.query, key) or "", cursor=0)

    return apply


def _focus(target: Focus) -> Callable[[PickerState], PickerState]:
    def apply(state: PickerState) -> PickerState:
        if target == "list" and not visible(state):
            return state
        if target == "title" and not state.request.title_label:
            return state
        return replace(state, focus=target)

    return apply


def _clear_query(state: PickerState) -> PickerState:
    return replace(state, query="", cursor=0, focus="search")


def _list_up(state: PickerState) -> PickerState:
    if state.cursor <= 0:
        return replace(state, focus="search")
    return replace(state, cursor=state.cursor - 1)


def _list_down(state: PickerState) -> PickerState:
    last = len(visible(state)) - 1
    return replace(state, cursor=max(0, min(state.cursor + 1, last)))


def _toggle(state: PickerState) -> PickerState:
    ranked = visible(state)
    if not ranked:
        return state
    item = ranked[min(state.cursor, len(ranked) - 1)].item
    if item.id in state.selected:
        return _deselect(state, item.id)
    return replace(state, selected=(*state.selected, item.id), known={**state.known, item.id: item})


def _deselect(state: PickerState, item_id: str) -> PickerState:
    overrides = {owner: own for owner, own in state.overrides.items() if owner != item_id}
    row = (ALL, None) if state.row[0] == item_id else state.row
    selected = tuple(other for other in state.selected if other != item_id)
    return replace(state, selected=selected, overrides=overrides, row=row)


def _tab(state: PickerState) -> PickerState:
    if state.focus == "selected":
        return replace(state, focus="search")
    return replace(state, focus="selected")


def _move_row(step: int) -> Callable[[PickerState], PickerState]:
    def apply(state: PickerState) -> PickerState:
        all_rows = rows(state)
        index = all_rows.index(state.row) if state.row in all_rows else 0
        target = all_rows[max(0, min(index + step, len(all_rows) - 1))]
        return replace(state, row=target)

    return apply


def _setting(state: PickerState, key: str) -> PickSetting:
    return next(setting for setting in state.request.settings if setting.key == key)


def _set_value(state: PickerState, owner: str, key: str, value: str) -> PickerState:
    if owner == ALL:
        return replace(state, defaults={**state.defaults, key: value})
    own = {k: v for k, v in state.overrides.get(owner, {}).items() if k != key}
    if value != state.defaults[key]:
        own[key] = value
    overrides = {k: v for k, v in state.overrides.items() if k != owner}
    if own:
        overrides[owner] = own
    return replace(state, overrides=overrides)


def _cycle(step: int) -> Callable[[PickerState], PickerState]:
    def apply(state: PickerState) -> PickerState:
        owner, key = state.row
        if key is None or owner == CREATE:
            return state
        choices = _setting(state, key).choices
        if not choices:
            return state
        current = setting_value(state, owner, key)
        index = choices.index(current) if current in choices else -1
        return _set_value(state, owner, key, choices[(index + step) % len(choices)])

    return apply


def _activate(state: PickerState) -> PickerState:
    owner, key = state.row
    if owner == CREATE:
        return _confirm(state)
    if key is None:
        return state
    if _setting(state, key).choices:
        return _cycle(1)(state)
    return replace(state, editing=setting_value(state, owner, key))


def _remove_row_owner(state: PickerState) -> PickerState:
    owner, key = state.row
    if key is None and owner not in (ALL, CREATE):
        return _deselect(state, owner)
    return state


def _edit_key(state: PickerState, key: str) -> PickerState:
    buffer = state.editing or ""
    owner, setting_key = state.row
    if key == "enter" or key == "ctrl-s":
        assert setting_key is not None
        committed = replace(_set_value(state, owner, setting_key, buffer.strip()), editing=None)
        return _confirm(committed) if key == "ctrl-s" else committed
    if key == "esc":
        return replace(state, editing=None)
    if key == "ctrl-c":
        return _ctrl_c(replace(state, editing=None))
    if key == "tab":
        candidates = completions(state)
        return replace(state, editing=candidates[0]) if candidates else state
    edited = _edit_text(buffer, key)
    if edited is not None:
        return replace(state, editing=edited)
    if len(key) == 1 and key.isprintable():
        return replace(state, editing=buffer + key)
    return state


def _confirm(state: PickerState) -> PickerState:
    if state.request.title_label and not state.title.strip():
        return replace(state, focus="title", error=f"{state.request.title_label} is required")
    if state.request.title_label and state.request.validate_title is not None:
        problem = state.request.validate_title(state.title.strip())
        if problem:
            return replace(state, focus="title", error=problem)
    if not state.selected:
        return replace(state, error="select at least one item")
    return replace(state, outcome="confirmed")


def _ctrl_c(state: PickerState) -> PickerState:
    if not state.selected:
        return replace(state, outcome="cancelled")
    return replace(state, quitting=True)


def _answer_quit(state: PickerState, key: str) -> PickerState:
    if key in ("y", "Y", "ctrl-c"):
        return replace(state, outcome="cancelled")
    return replace(state, quitting=False)


_Action = Callable[[PickerState], PickerState]

_GLOBAL: dict[str, _Action] = {"tab": _tab, "ctrl-s": _confirm, "ctrl-c": _ctrl_c}

_BY_FOCUS: dict[Focus, dict[str, _Action]] = {
    "title": {
        "enter": _focus("search"),
        "down": _focus("search"),
        "backspace": _edit_title("backspace"),
        "ctrl-u": _edit_title("ctrl-u"),
        "ctrl-w": _edit_title("ctrl-w"),
    },
    "search": {
        "down": _focus("list"),
        "enter": _focus("list"),
        "up": _focus("title"),
        "esc": _clear_query,
        "backspace": _edit_query("backspace"),
        "ctrl-u": _edit_query("ctrl-u"),
        "ctrl-w": _edit_query("ctrl-w"),
    },
    "list": {
        "up": _list_up,
        "down": _list_down,
        " ": _toggle,
        "enter": _toggle,
        "esc": _clear_query,
        "/": _focus("search"),
        "backspace": _edit_query("backspace"),
        "ctrl-u": _edit_query("ctrl-u"),
        "ctrl-w": _edit_query("ctrl-w"),
    },
    "selected": {
        "up": _move_row(-1),
        "down": _move_row(1),
        "left": _cycle(-1),
        "right": _cycle(1),
        "enter": _activate,
        " ": _remove_row_owner,
        "delete": _remove_row_owner,
    },
}
