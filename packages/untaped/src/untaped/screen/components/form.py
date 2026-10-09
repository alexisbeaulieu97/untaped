"""``Form``: fields in focus order with tab navigation, per-field validation and submit.

A form is a column of named fields. Tab and shift-tab move focus through them
(never wrapping, so the form hands the move to its parent at either end), every
other message goes to the focused field first, and ``Submit`` or an unhandled
``Activate`` validates every field, shows each error, focuses the first bad
one and, when all pass, sends :class:`Submitted` with the values. A form taller
than the frame shows the lines around the focused field.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Self

from rich.console import RenderableType
from rich.text import Text

from untaped.screen.components.buttons import Buttons
from untaped.screen.components.draw import role_style
from untaped.screen.components.fields import Field
from untaped.screen.components.layout import windowed
from untaped.screen.core import Activate, Cmd, Frame, NextField, PrevField, Submit
from untaped.stability import experimental

__all__ = ["Form", "Submitted"]


@experimental
@dataclass(frozen=True)
class Submitted:
    """Every field of a :class:`Form` passed validation; ``values`` maps field names to values.

    A ``SecretStr`` stays a ``SecretStr``, and a :class:`Buttons` row holds no
    value, so it is left out.
    """

    values: Mapping[str, object]


@experimental
@dataclass(frozen=True)
class Form:
    """Named fields drawn one under the other; ``focus`` is the index of the focused one.

    ``value`` maps each field's name to its value. Messages go to the focused
    field first, so a field that consumes a key (a completion list taking tab, an
    open ``Select`` taking esc) keeps it; ``NextField`` and ``PrevField`` it did
    not use move the focus, one step and never past either end. ``Submit``, and
    ``Activate`` that no field used (enter on a text field, not on a button),
    run :meth:`validate` on every field, put each result on its field, focus the
    first bad one and, when none is bad, send :class:`Submitted`. ``error`` is a
    form-level message shown under the fields.
    """

    fields: tuple[tuple[str, Field], ...]
    focus: int = 0
    error: str = ""

    def __post_init__(self) -> None:
        names = [name for name, _ in self.fields]
        if len(set(names)) != len(names):
            raise ValueError(f"a form needs distinct field names, got {names}")
        object.__setattr__(self, "focus", max(0, min(self.focus, len(self.fields) - 1)))

    @property
    def value(self) -> dict[str, object]:
        """Each field's value by name (a ``Buttons`` row is not a value)."""
        return {name: field.value for name, field in self.fields if not isinstance(field, Buttons)}

    def with_error(self, text: str) -> Self:
        """This form showing ``text`` under its fields (empty clears it)."""
        return replace(self, error=text)

    def validate(self) -> str:
        """The first field's error (empty when every field is fine); sets nothing."""
        return next((error for _, field in self.fields if (error := field.validate())), "")

    def update(self, message: object) -> tuple[Self, list[Cmd]]:
        """Submit, or hand ``message`` to the focused field and move the focus if it passed."""
        if isinstance(message, Submit):
            return self._submit()
        if not self.fields:
            return self, []
        _, field = self.fields[self.focus]
        updated, cmds = field.update(message)
        if updated is not field or cmds:
            return self._with(self.focus, updated), list(cmds)
        match message:
            case NextField() if self.focus + 1 < len(self.fields):
                return replace(self, focus=self.focus + 1), []
            case PrevField() if self.focus > 0:
                return replace(self, focus=self.focus - 1), []
            case Activate():
                return self._submit()
        return self, []

    def _with(self, slot: int, field: Field) -> Self:
        fields = tuple(
            (name, field if index == slot else current)
            for index, (name, current) in enumerate(self.fields)
        )
        return replace(self, fields=fields)

    def _submit(self) -> tuple[Self, list[Cmd]]:
        errors = [field.validate() for _, field in self.fields]
        checked = replace(
            self,
            fields=tuple(
                (name, field.with_error(error))
                for (name, field), error in zip(self.fields, errors, strict=True)
            ),
            error="",
        )
        bad = next((index for index, error in enumerate(errors) if error), None)
        if bad is not None:
            return replace(checked, focus=bad), []
        return checked, [Cmd.send(Submitted(self.value))]

    def view(
        self, frame: Frame, *, focused: bool = False, width: int | None = None
    ) -> RenderableType:
        """The fields stacked a blank line apart, cut to the lines around the focused one."""
        blocks: list[RenderableType] = [
            field.view(frame, focused=focused and index == self.focus, width=width)
            for index, (_, field) in enumerate(self.fields)
        ]
        if self.error:
            blocks.append(Text(self.error, style=role_style(frame, "screen.error")))
        return windowed(blocks, self.focus, frame.height)
