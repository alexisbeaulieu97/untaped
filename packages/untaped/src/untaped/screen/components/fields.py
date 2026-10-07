"""The component contract (:class:`Field`) and :func:`field_for`, which maps a setting to one.

A component is a frozen dataclass: its constructor is its ``init``,
``update(message)`` returns the next component and any commands, and
``view(frame, focused=..., width=...)`` draws it. ``update`` hands back the same
object for a message it does not consume; that identity is how the runtime
knows whether a key was handled, so a component never returns an equal copy
for a key it ignores. Focus belongs to the parent (a form, a tab strip, the
screen), which passes it to ``view``.
"""

from __future__ import annotations

from typing import Protocol, Self

from rich.console import RenderableType

from untaped.screen.core import Cmd, Frame
from untaped.stability import experimental

__all__ = ["Field"]


@experimental
class Field(Protocol):
    """What every component offers a parent that holds it."""

    @property
    def value(self) -> object:
        """What the component currently holds (a string, a number, a ``SecretStr``, ...)."""
        ...

    @property
    def error(self) -> str:
        """The error text it shows (empty when none)."""
        ...

    def update(self, message: object) -> tuple[Self, list[Cmd]]:
        """The component after ``message``: ``self`` itself when it consumed nothing."""
        ...

    def view(
        self, frame: Frame, *, focused: bool = False, width: int | None = None
    ) -> RenderableType:
        """The component drawn ``width`` cells wide (the frame's width by default)."""
        ...

    def with_error(self, text: str) -> Self:
        """The component showing ``text`` as its error (empty clears it)."""
        ...

    def validate(self) -> str:
        """The error text for what it holds now (empty when it is fine); never sets it."""
        ...
