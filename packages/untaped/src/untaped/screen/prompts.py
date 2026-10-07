"""The one-shot prompts (text, secret, select, multiselect, confirm) as inline screens.

``ui.text``, ``ui.secret``, ``ui.select``, ``ui.multiselect`` and ``ui.confirm``
ask one question each, so each is a small ``layout="inline"`` screen built from
the same components as every other screen (``TextInput``, ``SecretInput``,
``SingleList``, ``SearchList``, ``MultiList``): one prompt stack, one look, the
theme's symbols, colors and border. :class:`~untaped.prompts.PromptToolkitPromptBackend`
builds a screen and runs it; nothing here touches a terminal.

The question is the box's label. A question too long for the border (or one that
spans lines) is drawn above an unlabelled box instead. How a prompt ends maps
onto what the line prompts did: enter answers, esc and ctrl-d end it without an
answer (``EOFError`` at the backend, exit 1) and ctrl-c is an interrupt (exit
130). A ``secret_screen`` quits with the secrets as ``SecretStr`` so nothing
secret is ever a frame or a ``repr``.

The screens say ``this prompt`` where a screen names its command: a prompt
always has a terminal already (the backend's streams), so the no-terminal message
is never shown for them.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any

from pydantic import SecretStr
from rich.cells import cell_len
from rich.console import Group, RenderableType
from rich.text import Text

from untaped.prompts import PromptChoice
from untaped.screen.components.choices import ListItem, MultiList, SingleList
from untaped.screen.components.draw import role_style
from untaped.screen.components.inputs import SecretInput, TextInput
from untaped.screen.components.lists import SearchList
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
#: never shown, because the backend's streams are already a terminal.
PROMPT_COMMAND = "this prompt"
PROMPT_ALTERNATIVE = "the command's options"

#: The widest a prompt's box grows; a wider terminal does not stretch a one-line question.
BOX_WIDTH = 72
#: What a box's top border spends beside the label: the corners, a rule and the padding.
_LABEL_MARGIN = 6

CONFIRM_RETRY = "Please answer y or n."
CONFIRM_YES = ("y", "yes")
CONFIRM_NO = ("n", "no")

type _Labelled = TextInput | SecretInput | SingleList | SearchList | MultiList


@dataclass(frozen=True)
class _EndOfInput:
    """Ctrl-d on an empty answer: end the prompt the way end-of-file does."""


def _end_of_input(when: Callable[[Any], bool] | None = None) -> Binding:
    return Binding("ctrl-d", "cancel", _EndOfInput(), when=when)


def _draw(frame: Frame, field: _Labelled, *, focused: bool = True) -> RenderableType:
    """``field`` in a box no wider than :data:`BOX_WIDTH`, its question as the label if it fits."""
    width = min(frame.width, BOX_WIDTH)
    label = field.label
    if "\n" not in label and cell_len(label) + _LABEL_MARGIN <= width:
        return field.view(frame, focused=focused, width=width)
    heading = Text(label, style=role_style(frame, "screen.accent"))
    return Group(heading, replace(field, label="").view(frame, focused=focused, width=width))


def _forwarded[F: _Labelled](field: F, message: object) -> tuple[F, list[Cmd]]:
    """``message`` handed to ``field``: the field and its commands (the same field if it passed)."""
    return field.update(message)  # type: ignore[return-value]


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
            case _EndOfInput():
                return model, [Cmd.send(Cancel())]
        field, cmds = _forwarded(model.field, msg)
        return (model if field is model.field else replace(model, field=field)), cmds

    return Screen(
        init=lambda: (_Text(TextInput(message, default or "")), []),
        update=update,
        view=lambda model, frame: _draw(frame, model.field),
        title=message,
        command=PROMPT_COMMAND,
        alternative=PROMPT_ALTERNATIVE,
        keys=(_end_of_input(lambda model: not model.field.value),),
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
            case _EndOfInput():
                return model, [Cmd.send(Cancel())]
        field, cmds = _forwarded(model.focused, msg)
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
        update=update,
        view=view,
        title=message,
        command=PROMPT_COMMAND,
        alternative=PROMPT_ALTERNATIVE,
        keys=(_end_of_input(lambda model: not len(model.focused.value)),),
        layout="inline",
    )


# --- choices ------------------------------------------------------------------


def _items[T](choices: Sequence[PromptChoice[T]]) -> tuple[ListItem, ...]:
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


def _index_of[T](choices: Sequence[PromptChoice[T]], value: T | None) -> int | None:
    if value is None:
        return None
    return next((index for index, item in enumerate(choices) if item.value == value), None)


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


def select_screen[T](
    message: str,
    choices: Sequence[PromptChoice[T]],
    default: T | None = None,
    *,
    search: bool = False,
) -> Screen[_Choose, T]:
    """One choice: up and down move, enter answers with the value of the row under the cursor.

    With ``search`` the list is a ``SearchList``: typing filters it (fuzzy), so a long list is
    reached by typing. The ``default`` is marked and starts under the cursor.
    """
    items = _items(choices)
    default_index = _index_of(choices, default)

    def init() -> tuple[_Choose, list[Cmd]]:
        if search:
            picked = frozenset() if default_index is None else frozenset({str(default_index)})
            return _Choose(
                SearchList(message, items, cursor=default_index or 0, selected=picked)
            ), []
        return _Choose(
            SingleList(message, items, "" if default_index is None else str(default_index))
        ), []

    def update(model: _Choose, msg: object) -> tuple[_Choose, list[Cmd]]:
        match msg:
            case _EndOfInput():
                return model, [Cmd.send(Cancel())]
            case Key("enter"):
                chosen = model.under_cursor
                if chosen is None:
                    return model, []
                return model, [Cmd.send(Quit(choices[int(chosen)].value))]
        field, cmds = _forwarded(model.field, msg)
        return (model if field is model.field else replace(model, field=field)), cmds

    return Screen(
        init=init,
        update=update,
        view=lambda model, frame: _draw(frame, model.field),
        title=message,
        command=PROMPT_COMMAND,
        alternative=PROMPT_ALTERNATIVE,
        keys=(_end_of_input(lambda model: not _typing(model)),),
        layout="inline",
    )


def _typing(model: _Choose) -> bool:
    """Whether a search query is being typed (ctrl-d then waits, as it does in a text line)."""
    return isinstance(model.field, SearchList) and bool(model.field.query)


@dataclass(frozen=True)
class _Multi:
    field: MultiList


def multiselect_screen[T](
    message: str, choices: Sequence[PromptChoice[T]], defaults: Sequence[T] = ()
) -> Screen[_Multi, list[T]]:
    """Several choices: space toggles the row under the cursor, enter answers with those checked."""
    items = _items(choices)
    checked = frozenset(str(index) for index, item in enumerate(choices) if item.value in defaults)

    def update(model: _Multi, msg: object) -> tuple[_Multi, list[Cmd]]:
        match msg:
            case Activate():
                values = [choices[int(item_id)].value for item_id in model.field.value]
                return model, [Cmd.send(Quit(values))]
            case _EndOfInput():
                return model, [Cmd.send(Cancel())]
        field, cmds = _forwarded(model.field, msg)
        return (model if field is model.field else replace(model, field=field)), cmds

    return Screen(
        init=lambda: (_Multi(MultiList(message, items, selected=checked)), []),
        update=update,
        view=lambda model, frame: _draw(frame, model.field),
        title=message,
        command=PROMPT_COMMAND,
        alternative=PROMPT_ALTERNATIVE,
        keys=(Binding(" ", "toggle", None), _end_of_input()),
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
            case _EndOfInput():
                return model, [Cmd.send(Cancel())]
        field, cmds = _forwarded(model.field, msg)
        return (model if field is model.field else replace(model, field=field)), cmds

    return Screen(
        # Empty, so enter takes the default: a prefilled "n" would turn a typed "y" into "ny".
        init=lambda: (_Confirm(TextInput(label), default), []),
        update=update,
        view=lambda model, frame: _draw(frame, model.field),
        title=message,
        command=PROMPT_COMMAND,
        alternative=PROMPT_ALTERNATIVE,
        keys=(_end_of_input(lambda model: not model.field.value),),
        layout="inline",
    )
