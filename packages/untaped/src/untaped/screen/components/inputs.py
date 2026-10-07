"""The text components: ``TextInput``, ``PathInput``, ``SecretInput`` and ``NumberInput``.

All four are one line in a labelled box with a drawn caret, a muted help line
and a red border and message for an error; they edit through
:class:`~untaped.screen.components.text.EditBuffer`. They consume a key only
when it edits (a ``?`` is text, a tab is not unless a completion is open) and
return themselves, the same object, for anything else, which is what lets the
runtime hand the key to the screen's bindings and the shared keys.

``SecretInput`` holds a ``SecretStr``: the plain text is read only inside
``update`` to apply an edit, and the view draws the theme's mask symbol once
per character, so the secret never reaches a frame or a repr.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Self

from pydantic import SecretStr
from rich.console import Group, RenderableType
from rich.text import Text

from untaped.screen.components.box import field_box
from untaped.screen.components.draw import (
    divider,
    inner_width,
    option_row,
    role_style,
    text_line,
    window_start,
)
from untaped.screen.components.paths import complete_paths
from untaped.screen.components.text import EditBuffer
from untaped.screen.core import Cmd, Frame, Key, Paste
from untaped.stability import experimental

__all__ = ["NumberInput", "PathInput", "SecretInput", "TextInput"]

#: How many completion candidates show at once.
COMPLETION_ROWS = 5
#: The characters a ``NumberInput`` accepts when typed or pasted.
_INTEGER_CHARS = "0123456789+-"
_FLOAT_CHARS = "0123456789+-.eE"

type Completer = Callable[[str], Sequence[str]]


def _position(cursor: int | None, length: int) -> int:
    """The caret's cell: the end when ``cursor`` is ``None``, otherwise kept inside the text."""
    return length if cursor is None else max(0, min(cursor, length))


@experimental
@dataclass(frozen=True)
class TextInput:
    """A line of text in a labelled box.

    ``validator`` takes the value and returns the error text (empty when it is
    fine); it runs on :meth:`validate`, never while typing. ``complete`` takes
    the value and returns the full strings that could replace it: they show in
    the box under the value while the field is focused, up and down choose one,
    tab accepts it and esc closes the list. With no list open, tab and esc are
    left to the screen (next field, back).

    Nothing is offered until the user edits the value, so a value the field
    starts with (a saved path, a default) is never rewritten by a tab that was
    meant to move on; the candidates are kept with the text they were computed
    for and ignored once the value is something else.
    """

    label: str
    value: str = ""
    cursor: int | None = None
    help: str = ""
    error: str = ""
    placeholder: str = ""
    validator: Callable[[str], str] | None = None
    complete: Completer | None = None
    matches: tuple[str, ...] = field(default=(), repr=False)
    matched: str | None = field(default=None, repr=False)
    choice: int = field(default=0, repr=False)
    dismissed: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "cursor", _position(self.cursor, len(self.value)))

    def _candidates(self, text: str) -> tuple[str, ...]:
        if self.complete is None:
            return ()
        return tuple(candidate for candidate in self.complete(text) if candidate != text)

    def _open_matches(self) -> tuple[str, ...]:
        """The candidates for the value as it is now: none when they were computed for other text.

        ``matches`` is stored together with the text it was computed for
        (``matched``); a value changed behind the component's back (``replace(field,
        value=...)``) leaves them stale, and stale candidates are never offered.
        """
        return self.matches if self.matched == self.value else ()

    @property
    def completing(self) -> bool:
        """Whether a completion list is open (there are candidates and esc did not close it)."""
        return bool(self._open_matches()) and not self.dismissed

    def with_error(self, text: str) -> Self:
        """This input showing ``text`` as its error (empty clears it)."""
        return replace(self, error=text)

    def validate(self) -> str:
        """The error text for the current value (empty when it is fine)."""
        return self.validator(self.value) if self.validator is not None else ""

    def update(self, message: object) -> tuple[Self, list[Cmd]]:
        """Apply an editing key or a paste, or a completion key while the list is open."""
        if isinstance(message, Key) and self.completing:
            chosen = self._complete_key(message.name)
            if chosen is not None:
                return chosen, []
        buffer = EditBuffer(self.value, _position(self.cursor, len(self.value)))
        match message:
            case Key(name):
                edited = buffer.key(name)
            case Paste(text):
                edited = buffer.paste(text)
            case _:
                return self, []
        if edited is None or edited == buffer:
            return self, []
        if edited.text == self.value:
            return replace(self, cursor=edited.cursor), []
        return self._with_text(edited.text, edited.cursor), []

    def _with_text(self, text: str, cursor: int) -> Self:
        return replace(
            self,
            value=text,
            cursor=cursor,
            error="",
            matches=self._candidates(text),
            matched=text,
            choice=0,
            dismissed=False,
        )

    def _complete_key(self, name: str) -> Self | None:
        matches = self._open_matches()
        choice = min(self.choice, len(matches) - 1)
        if name == "tab":
            text = matches[choice]
            return self._with_text(text, len(text))
        if name == "esc":
            return replace(self, dismissed=True)
        if name in ("up", "down"):
            moved = max(0, min(choice + (1 if name == "down" else -1), len(matches) - 1))
            return replace(self, choice=moved) if moved != self.choice else self
        return None

    def view(
        self, frame: Frame, *, focused: bool = False, width: int | None = None
    ) -> RenderableType:
        """The box: the value with its caret and, while completing, the candidates below."""
        inner = inner_width(frame, width)
        body: list[RenderableType] = [
            text_line(
                frame,
                inner,
                self.value,
                _position(self.cursor, len(self.value)),
                focused=focused,
                placeholder=self.placeholder,
            )
        ]
        if focused and self.completing:
            matches = self._open_matches()
            body.extend(_candidate_rows(frame, inner, matches, min(self.choice, len(matches) - 1)))
        return field_box(
            frame,
            self.label,
            Group(*body),
            focused=focused,
            error=self.error,
            help=self.help,
            width=width,
        )


def _candidate_rows(
    frame: Frame, inner: int, matches: tuple[str, ...], choice: int
) -> list[RenderableType]:
    rows: list[RenderableType] = []
    rule = divider(frame, inner)
    if rule is not None:
        rows.append(rule)
    start = window_start(len(matches), choice, COMPLETION_ROWS)
    for index in range(start, min(start + COMPLETION_ROWS, len(matches))):
        cursor = index == choice
        rows.append(
            option_row(
                frame,
                inner,
                label=matches[index],
                label_style=role_style(frame, "screen.highlight" if cursor else "screen.muted"),
                base=role_style(frame, "screen.highlight") if cursor else None,
            )
        )
    return rows


@experimental
@dataclass(frozen=True)
class PathInput(TextInput):
    """A :class:`TextInput` that completes filesystem paths.

    Directories come first and end in a separator; the text the user typed
    (``~`` included) is kept, only the last segment is completed. The
    directory is listed on every edit, synchronously, but only as far as
    :data:`~untaped.screen.components.paths.MAX_SCANNED` entries, so a huge
    directory keeps typing cheap (and may not list every match).
    """

    complete: Completer | None = complete_paths


@experimental
@dataclass(frozen=True)
class SecretInput:
    """A secret in a labelled box, masked with the theme's ``mask`` symbol.

    The value is a ``SecretStr``. Its plain text is read inside :meth:`update`
    only, to apply an edit; the view draws one mask symbol per character, so
    neither a frame nor ``repr`` ever shows it.
    """

    label: str
    value: SecretStr = field(default_factory=lambda: SecretStr(""))
    cursor: int | None = None
    help: str = ""
    error: str = ""
    placeholder: str = ""
    validator: Callable[[SecretStr], str] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "cursor", _position(self.cursor, len(self.value)))

    def with_error(self, text: str) -> Self:
        """This input showing ``text`` as its error (empty clears it)."""
        return replace(self, error=text)

    def validate(self) -> str:
        """The error text for the current value (empty when it is fine)."""
        return self.validator(self.value) if self.validator is not None else ""

    def update(self, message: object) -> tuple[Self, list[Cmd]]:
        """Apply an editing key or a paste."""
        buffer = EditBuffer(self.value.get_secret_value(), _position(self.cursor, len(self.value)))
        match message:
            case Key(name):
                edited = buffer.key(name)
            case Paste(text):
                edited = buffer.paste(text)
            case _:
                return self, []
        if edited is None or edited == buffer:
            return self, []
        if edited.text == buffer.text:
            return replace(self, cursor=edited.cursor), []
        return replace(self, value=SecretStr(edited.text), cursor=edited.cursor, error=""), []

    def view(
        self, frame: Frame, *, focused: bool = False, width: int | None = None
    ) -> RenderableType:
        """The box: one mask symbol per character, with the caret."""
        inner = inner_width(frame, width)
        masked = frame.symbol("mask") * len(self.value)
        line = text_line(
            frame,
            inner,
            masked,
            _position(self.cursor, len(self.value)) * len(frame.symbol("mask")),
            focused=focused,
            placeholder=self.placeholder,
        )
        return field_box(
            frame,
            self.label,
            line,
            focused=focused,
            error=self.error,
            help=self.help,
            width=width,
        )


@experimental
@dataclass(frozen=True)
class NumberInput:
    """A number in a labelled box; the text is what is typed, ``value`` what it parses to.

    Only digits, signs and (for ``integer=False``) the decimal point and
    exponent can be typed; a paste is accepted whole or not at all (``1.5``
    pasted into an integer field is ignored, not turned into ``15``).
    :meth:`validate` reports text that does not parse and a number outside
    ``minimum`` and ``maximum`` (both inclusive); an empty field is valid and
    its ``value`` is ``None``.
    """

    label: str
    text: str = ""
    minimum: float | None = None
    maximum: float | None = None
    integer: bool = True
    cursor: int | None = None
    help: str = ""
    error: str = ""
    placeholder: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "cursor", _position(self.cursor, len(self.text)))

    @property
    def value(self) -> int | float | None:
        """The parsed number, or ``None`` when the field is empty or does not parse."""
        return self._parse()

    def _parse(self) -> int | float | None:
        text = self.text.strip()
        try:
            if self.integer:
                return int(text)
            number = float(text)
        except ValueError:
            return None
        return number if math.isfinite(number) else None

    def with_error(self, text: str) -> Self:
        """This input showing ``text`` as its error (empty clears it)."""
        return replace(self, error=text)

    def validate(self) -> str:
        """The error text for the current text (empty when it is fine or empty)."""
        if not self.text.strip():
            return ""
        number = self._parse()
        if number is None:
            return "Must be a whole number." if self.integer else "Must be a number."
        low, high = self.minimum, self.maximum
        if low is not None and high is not None and not low <= number <= high:
            return f"Must be between {_shown(low)} and {_shown(high)}."
        if low is not None and number < low:
            return f"Must be at least {_shown(low)}."
        if high is not None and number > high:
            return f"Must be at most {_shown(high)}."
        return ""

    def update(self, message: object) -> tuple[Self, list[Cmd]]:
        """Apply an editing key or a paste; what a number cannot hold is ignored whole."""
        allowed = _INTEGER_CHARS if self.integer else _FLOAT_CHARS
        buffer = EditBuffer(self.text, _position(self.cursor, len(self.text)))
        match message:
            case Key(name):
                edited = buffer.key(name, allowed=allowed)
            case Paste(text):
                edited = buffer.paste(text, allowed=allowed)
            case _:
                return self, []
        if edited is None or edited == buffer:
            return self, []
        if edited.text == self.text:
            return replace(self, cursor=edited.cursor), []
        return replace(self, text=edited.text, cursor=edited.cursor, error=""), []

    def view(
        self, frame: Frame, *, focused: bool = False, width: int | None = None
    ) -> RenderableType:
        """The box: the text with its caret."""
        line: Text = text_line(
            frame,
            inner_width(frame, width),
            self.text,
            _position(self.cursor, len(self.text)),
            focused=focused,
            placeholder=self.placeholder,
        )
        return field_box(
            frame,
            self.label,
            line,
            focused=focused,
            error=self.error,
            help=self.help,
            width=width,
        )


def _shown(number: float) -> str:
    return str(int(number)) if float(number).is_integer() else f"{number:g}"
