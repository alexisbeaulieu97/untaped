"""The one-shot prompts (text, secret, select, multiselect, confirm) as inline screens.

``ui.text``, ``ui.secret``, ``ui.select``, ``ui.multiselect`` and ``ui.confirm``
ask one question each, so each is a small ``layout="inline"`` screen built from
the same components as every other screen (``TextInput``, ``SecretInput``,
``SingleList``, ``SearchList``, ``MultiList``): one prompt stack, one look, the
theme's symbols, colors and border. :class:`~untaped.prompts.PromptToolkitPromptBackend`
builds a screen and runs it; nothing here touches a terminal. ``select_screen`` and
``multiselect_screen`` take the caller's choices and answer with positions, so the
backend maps an answer back to its choice (two rows may share a value).

The question is the box's label. A question too long for the border (or one that
spans lines) is drawn above an unlabelled box instead. How a prompt ends maps
onto what the line prompts did: enter answers, esc and ctrl-d end it without an
answer (``EOFError`` at the backend, exit 1) and ctrl-c is an interrupt (exit
130). A ``secret_screen`` quits with the secrets as ``SecretStr`` so nothing
secret is ever a frame or a ``repr``.

The screens say ``this prompt`` where a screen names its command: ``UiContext`` secures
the terminal (stdin and stderr, the controlling terminal when either is redirected)
before a prompt runs and refuses with its own message, so the no-terminal message of a
screen is never shown for them. Esc cancels a prompt, so the footer says ``esc cancel``
(``esc clear`` while a search query is typed, which esc clears first).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any

from pydantic import SecretStr
from rich.cells import cell_len
from rich.console import Group, RenderableType
from rich.text import Text

from untaped.prompts import PromptChoice
from untaped.screen.components.choices import ListItem, MultiList, SingleList, rows_budget
from untaped.screen.components.draw import role_style
from untaped.screen.components.inputs import SecretInput, TextInput
from untaped.screen.components.lists import SearchList, default_rows
from untaped.screen.core import (
    Activate,
    Binding,
    Cancel,
    Cmd,
    Frame,
    Key,
    NextField,
    PrevField,
    Quit,
    Screen,
)

__all__ = [
    "confirm_screen",
    "multiselect_screen",
    "secret_screen",
    "select_screen",
    "text_screen",
]

#: What a prompt screen names where other screens name their command and alternative;
#: never shown, because ``UiContext`` has secured a terminal before a prompt runs.
PROMPT_COMMAND = "this prompt"
PROMPT_ALTERNATIVE = "the command's options"

#: The widest a prompt's box grows; a wider terminal does not stretch a one-line question.
BOX_WIDTH = 72
#: What a box's top border spends beside the label: the corners, a rule and the padding.
_LABEL_MARGIN = 6

CONFIRM_RETRY = "Please answer y or n."
CONFIRM_YES = ("y", "yes")
CONFIRM_NO = ("n", "no")

#: Esc cancels a one-shot prompt, so its footer says so instead of ``esc back``.
_CANCEL_LABEL = MappingProxyType({"esc": "cancel"})

type _Labelled = TextInput | SecretInput | SingleList | SearchList | MultiList


@dataclass(frozen=True)
class _EndOfInput:
    """Ctrl-d on an empty answer: end the prompt the way end-of-file does."""


def _end_of_input(when: Callable[[Any], bool] | None = None) -> Binding:
    return Binding("ctrl-d", "cancel", _EndOfInput(), when=when)


#: What a list's box spends around its rows: the border, and for a search list also the
#: search line, its rule and the count line.
_LIST_CHROME = 2
_SEARCH_CHROME = 5


def _fitted(frame: Frame, field: _Labelled, *, spent: int) -> _Labelled:
    """``field`` with its window of rows cut down so the prompt fits the frame.

    ``spent`` is the lines the prompt draws besides the field's own box.
    """
    if isinstance(field, SingleList | MultiList):
        natural, chrome = rows_budget(frame), _LIST_CHROME
    elif isinstance(field, SearchList):
        natural, chrome = default_rows(frame), _SEARCH_CHROME
    else:
        return field
    rows = max(1, frame.height - spent - chrome)
    return replace(field, window_rows=rows) if rows < natural else field


def _draw(frame: Frame, field: _Labelled, *, focused: bool = True) -> RenderableType:
    """``field`` in a box no wider than :data:`BOX_WIDTH`, its question as the label if it fits."""
    width = min(frame.width, BOX_WIDTH)
    label = field.label
    if "\n" not in label and cell_len(label) + _LABEL_MARGIN <= width:
        return _fitted(frame, field, spent=0).view(frame, focused=focused, width=width)
    heading = Text(label, style=role_style(frame, "screen.accent"))
    spent = sum(-(-max(1, cell_len(line)) // width) for line in label.split("\n"))
    boxed = _fitted(frame, replace(field, label=""), spent=spent)
    return Group(heading, boxed.view(frame, focused=focused, width=width))


def _ends_on_end_of_input[M](
    update: Callable[[M, object], tuple[M, list[Cmd]]],
) -> Callable[[M, object], tuple[M, list[Cmd]]]:
    """``update`` that cancels the prompt on ctrl-d, which every prompt does the same way."""

    def wrapped(model: M, msg: object) -> tuple[M, list[Cmd]]:
        if isinstance(msg, _EndOfInput):
            return model, [Cmd.send(Cancel())]
        return update(model, msg)

    return wrapped


# --- text ---------------------------------------------------------------------


@dataclass(frozen=True)
class _Text:
    field: TextInput


def text_screen(message: str, default: str | None = None) -> Screen[_Text, str]:
    """A line of text: enter answers with exactly what is typed (not stripped)."""

    def update(model: _Text, msg: object) -> tuple[_Text, list[Cmd]]:
        match msg:
            case Activate():
                return model, [Cmd.send(Quit(model.field.value))]
        field, cmds = model.field.update(msg)
        return (model if field is model.field else replace(model, field=field)), cmds

    return Screen(
        init=lambda: (_Text(TextInput(message, default or "")), []),
        update=_ends_on_end_of_input(update),
        view=lambda model, frame: _draw(frame, model.field),
        title=message,
        command=PROMPT_COMMAND,
        alternative=PROMPT_ALTERNATIVE,
        keys=(_end_of_input(lambda model: not model.field.value),),
        shared_labels=_CANCEL_LABEL,
        layout="inline",
    )


# --- secret -------------------------------------------------------------------


@dataclass(frozen=True)
class _Secret:
    first: SecretInput
    second: SecretInput | None = None
    focus: int = 0

    @property
    def focused(self) -> SecretInput:
        return self.second if self.focus == 1 and self.second is not None else self.first


def secret_screen(
    message: str, *, confirmation: bool = False
) -> Screen[_Secret, tuple[SecretStr, SecretStr]]:
    """Hidden text: enter answers with ``(value, repeated)``, the same value twice when unasked.

    With ``confirmation`` a second ``Confirm value`` box follows: enter in the first (or
    tab) moves to it, enter in the second answers. The caller compares the two.
    """

    def update(model: _Secret, msg: object) -> tuple[_Secret, list[Cmd]]:
        match msg:
            case Activate():
                if model.second is not None and model.focus == 0:
                    return replace(model, focus=1), []
                repeated = model.second.value if model.second is not None else model.first.value
                return model, [Cmd.send(Quit((model.first.value, repeated)))]
            case NextField() | PrevField():
                if model.second is None:
                    return model, []
                return replace(model, focus=1 - model.focus), []
        field, cmds = model.focused.update(msg)
        if field is model.focused:
            return model, cmds
        if model.focus == 1:
            return replace(model, second=field), cmds
        return replace(model, first=field), cmds

    def view(model: _Secret, frame: Frame) -> RenderableType:
        boxes = [_draw(frame, model.first, focused=model.focus == 0)]
        if model.second is not None:
            boxes.append(_draw(frame, model.second, focused=model.focus == 1))
        return Group(*boxes)

    return Screen(
        init=lambda: (
            _Secret(SecretInput(message), SecretInput("Confirm value") if confirmation else None),
            [],
        ),
        update=_ends_on_end_of_input(update),
        view=view,
        title=message,
        command=PROMPT_COMMAND,
        alternative=PROMPT_ALTERNATIVE,
        keys=(_end_of_input(lambda model: not len(model.focused.value)),),
        shared_labels=_CANCEL_LABEL,
        layout="inline",
    )


# --- choices ------------------------------------------------------------------


def _items(choices: Sequence[PromptChoice[Any]]) -> tuple[ListItem, ...]:
    """One row per choice, identified by its position (labels are unique, values need not hash).

    The description is the row's muted detail on the right, and what a search matches besides
    the label.
    """
    return tuple(
        ListItem(
            str(index),
            item.label,
            detail=item.description or "",
            description=item.description or "",
        )
        for index, item in enumerate(choices)
    )


@dataclass(frozen=True)
class _Choose:
    field: SingleList | SearchList

    @property
    def under_cursor(self) -> str | None:
        """The id of the row the cursor is on, ``None`` when no row shows."""
        if isinstance(self.field, SearchList):
            matches = self.field.matches
            return matches[self.field.cursor].item.id if matches else None
        return self.field.items[self.field.cursor or 0].id if self.field.items else None


def select_screen(
    message: str,
    choices: Sequence[PromptChoice[Any]],
    default: int | None = None,
    *,
    search: bool = False,
) -> Screen[_Choose, int]:
    """One choice: up and down move, enter answers with the position of the row under the cursor.

    With ``search`` the list is a ``SearchList``: typing filters it (fuzzy), so a long list is
    reached by typing. The ``default`` (a position) is marked and starts under the cursor.
    """
    items = _items(choices)

    def init() -> tuple[_Choose, list[Cmd]]:
        if search:
            picked = frozenset() if default is None else frozenset({str(default)})
            return _Choose(SearchList(message, items, cursor=default or 0, selected=picked)), []
        return _Choose(SingleList(message, items, "" if default is None else str(default))), []

    def update(model: _Choose, msg: object) -> tuple[_Choose, list[Cmd]]:
        match msg:
            case Key("enter"):
                chosen = model.under_cursor
                if chosen is None:
                    return model, []
                return model, [Cmd.send(Quit(int(chosen)))]
        field, cmds = model.field.update(msg)
        return (model if field is model.field else replace(model, field=field)), cmds

    return Screen(
        init=init,
        update=_ends_on_end_of_input(update),
        view=lambda model, frame: _draw(frame, model.field),
        title=message,
        command=PROMPT_COMMAND,
        alternative=PROMPT_ALTERNATIVE,
        keys=(_end_of_input(lambda model: not _typing(model)),),
        shared_labels={"esc": _select_esc_label if search else "cancel"},
        layout="inline",
    )


def _typing(model: _Choose) -> bool:
    """Whether a search query is being typed (ctrl-d then waits, as it does in a text line)."""
    return isinstance(model.field, SearchList) and bool(model.field.query)


def _select_esc_label(model: _Choose) -> str:
    """Esc clears a typed query before it cancels the prompt."""
    return "clear" if _typing(model) else "cancel"


@dataclass(frozen=True)
class _Multi:
    field: MultiList


def multiselect_screen(
    message: str, choices: Sequence[PromptChoice[Any]], defaults: Sequence[int] = ()
) -> Screen[_Multi, list[int]]:
    """Several choices: space toggles a row, enter answers with the checked positions in order.

    ``defaults`` are the positions that start checked.
    """
    items = _items(choices)
    checked = frozenset(str(index) for index in defaults)

    def update(model: _Multi, msg: object) -> tuple[_Multi, list[Cmd]]:
        match msg:
            case Activate():
                return model, [Cmd.send(Quit([int(item_id) for item_id in model.field.value]))]
        field, cmds = model.field.update(msg)
        return (model if field is model.field else replace(model, field=field)), cmds

    return Screen(
        init=lambda: (_Multi(MultiList(message, items, selected=checked)), []),
        update=_ends_on_end_of_input(update),
        view=lambda model, frame: _draw(frame, model.field),
        title=message,
        command=PROMPT_COMMAND,
        alternative=PROMPT_ALTERNATIVE,
        keys=(Binding(" ", "toggle", None), _end_of_input()),
        shared_labels=_CANCEL_LABEL,
        layout="inline",
    )


# --- confirm ------------------------------------------------------------------


@dataclass(frozen=True)
class _Confirm:
    field: TextInput
    default: bool


def confirm_screen(message: str, default: bool = False) -> Screen[_Confirm, bool]:
    """A yes or no: enter alone takes the ``default``, ``y``/``yes``/``n``/``no`` (any case) answer.

    Anything else shows ``Please answer y or n.`` under the box and the prompt stays.
    """
    label = f"{message} {'[Y/n]' if default else '[y/N]'}"

    def update(model: _Confirm, msg: object) -> tuple[_Confirm, list[Cmd]]:
        match msg:
            case Activate():
                answer = model.field.value.strip().lower()
                if not answer:
                    return model, [Cmd.send(Quit(model.default))]
                if answer in CONFIRM_YES:
                    return model, [Cmd.send(Quit(True))]
                if answer in CONFIRM_NO:
                    return model, [Cmd.send(Quit(False))]
                return replace(model, field=model.field.with_error(CONFIRM_RETRY)), []
        field, cmds = model.field.update(msg)
        return (model if field is model.field else replace(model, field=field)), cmds

    return Screen(
        # Empty, so enter takes the default: a prefilled "n" would turn a typed "y" into "ny".
        init=lambda: (_Confirm(TextInput(label), default), []),
        update=_ends_on_end_of_input(update),
        view=lambda model, frame: _draw(frame, model.field),
        title=message,
        command=PROMPT_COMMAND,
        alternative=PROMPT_ALTERNATIVE,
        keys=(_end_of_input(lambda model: not model.field.value),),
        shared_labels=_CANCEL_LABEL,
        layout="inline",
    )
