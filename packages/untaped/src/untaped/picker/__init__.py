"""Public value types for the multi-select picker behind ``UiContext.pick_many``.

The picker lists :class:`PickItem` entries, lets the user select several and
set per-item :class:`PickSetting` values, and returns a :class:`PickResult`.
Nothing here imports prompt_toolkit or Rich: the picker is a screen
(:mod:`untaped.picker.screen`) that ``UiContext.pick_many`` runs on the screen
runtime, and its pure reducer is :mod:`untaped.picker.state`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True)
class PickItem:
    """One selectable entry; ``id`` is unique, ``dimmed`` entries sort last."""

    id: str
    label: str
    description: str = ""
    dimmed: bool = False


@dataclass(frozen=True)
class PickSetting:
    """One per-item setting shown in the Selected pane.

    With ``choices`` the value cycles with ←/→; otherwise it is free text
    edited with enter. ``placeholder`` is shown for an empty value.
    ``complete(item_id)`` returns completion candidates for a text field
    (``None`` for the all-items row). It is called on every render while the
    field is edited, so it must be cheap: cache anything slow.
    """

    key: str
    label: str
    default: str = ""
    choices: tuple[str, ...] = ()
    placeholder: str = ""
    complete: Callable[[str | None], Sequence[str]] | None = None


@dataclass(frozen=True)
class PickCatalog:
    """The items to list, plus a short footer note such as ``refreshed 2h ago``."""

    items: tuple[PickItem, ...]
    note: str = ""


@dataclass(frozen=True)
class PickRequest:
    """Everything the picker needs.

    ``title_label`` turns on an editable title field in the header (focused
    first while ``title`` is empty). ``validate_title(title)`` checks the
    stripped title on confirm; a returned message blocks it and is shown.
    ``subtitle(title, defaults)`` renders a live preview at the right of the
    header. ``refresh(force)`` runs in the background at start
    (``force=False``) and on ctrl-r (``force=True``); it is never called
    concurrently (ctrl-r is ignored while one runs), but may still be running
    after the picker returns. ``adhoc(query)`` may turn the typed query into
    an extra item (a URL). ``allow_empty`` lets the user confirm with nothing
    selected.

    ``command`` and ``alternative`` fill the refusal shown when there is no
    terminal to draw on (`` `command` needs a terminal; use `alternative` ``);
    left empty they fall back to a generic message, so a caller that gates on
    ``can_prompt`` itself can leave them out.
    """

    heading: str
    catalog: PickCatalog
    settings: tuple[PickSetting, ...] = ()
    title: str = ""
    title_label: str = ""
    validate_title: Callable[[str], str | None] | None = None
    subtitle: Callable[[str, Mapping[str, str]], str] | None = None
    refresh: Callable[[bool], PickCatalog] | None = None
    adhoc: Callable[[str], PickItem | None] | None = None
    allow_empty: bool = False
    command: str = ""
    alternative: str = ""


@dataclass(frozen=True)
class Picked:
    """One selected item with every setting resolved (overrides over defaults)."""

    item: PickItem
    settings: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PickResult:
    """What the user confirmed: the title, the all-items defaults, and the picks."""

    title: str
    defaults: Mapping[str, str]
    picks: tuple[Picked, ...]
