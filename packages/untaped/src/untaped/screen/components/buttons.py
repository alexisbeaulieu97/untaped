"""``Buttons``: a row of actions, primary and secondary in boxes, ghost as plain text.

Left and right move between the buttons; ``Activate`` (enter, once the keys
passed on it) presses the focused one by sending :class:`Pressed` with its
``id``, which the screen's ``update`` receives like any message. Without a
border (``border: none``) a button is its label, with the theme's ``chosen``
symbol before the focused one.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal, Self

from rich.cells import cell_len
from rich.console import RenderableType
from rich.padding import Padding
from rich.panel import Panel
from rich.style import Style
from rich.table import Table
from rich.text import Text

from untaped.screen.components.draw import BOLD, role_style
from untaped.screen.core import Activate, Cmd, Frame, Key
from untaped.stability import experimental

__all__ = ["Button", "Buttons", "Pressed"]


@experimental
@dataclass(frozen=True)
class Pressed:
    """The focused button was activated; ``id`` is that :class:`Button`'s ``id``."""

    id: str


@experimental
@dataclass(frozen=True)
class Button:
    """One action: a primary one is bold, a secondary one plain, a ghost one muted and unboxed."""

    id: str
    label: str
    kind: Literal["primary", "secondary", "ghost"] = "secondary"


@experimental
@dataclass(frozen=True)
class Buttons:
    """A row of :class:`Button` s; ``focus`` is the index of the focused one.

    ``value`` is the focused button's ``id``.
    """

    items: tuple[Button, ...]
    focus: int = 0
    error: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "focus", max(0, min(self.focus, len(self.items) - 1)))

    @property
    def value(self) -> str:
        """The ``id`` of the focused button (empty without buttons)."""
        return self.items[self.focus].id if self.items else ""

    def with_error(self, text: str) -> Self:
        """This row showing ``text`` under the buttons (empty clears it)."""
        return replace(self, error=text)

    def validate(self) -> str:
        """Always fine: buttons hold no input."""
        return ""

    def update(self, message: object) -> tuple[Self, list[Cmd]]:
        """Move the focus on left and right; press the focused button on ``Activate``."""
        if isinstance(message, Key) and message.name in ("left", "right") and self.items:
            focus = max(
                0, min(self.focus + (1 if message.name == "right" else -1), len(self.items) - 1)
            )
            return (replace(self, focus=focus) if focus != self.focus else self), []
        if isinstance(message, Activate) and self.items:
            return self, [Cmd.send(Pressed(self.items[self.focus].id))]
        return self, []

    def view(
        self, frame: Frame, *, focused: bool = False, width: int | None = None
    ) -> RenderableType:
        """The buttons side by side, the focused one's border in the focus colour."""
        row = self._boxed(frame, focused) or self._plain(frame, focused)
        if not self.error:
            return row
        table = Table.grid()
        table.add_row(row)
        table.add_row(Text(self.error, style=role_style(frame, "screen.error")))
        return table

    def _label_style(self, frame: Frame, button: Button, on: bool) -> Style:
        if on:
            return role_style(frame, "screen.accent")
        if button.kind == "ghost":
            return role_style(frame, "screen.muted")
        base = role_style(frame, "screen.value")
        return base + BOLD if button.kind == "primary" else base

    def _boxed(self, frame: Frame, focused: bool) -> RenderableType | None:
        outline = frame.box()
        if outline is None:
            return None
        grid = Table.grid(padding=(0, 1))
        cells: list[RenderableType] = []
        for index, button in enumerate(self.items):
            on = focused and index == self.focus
            label = Text(button.label, style=self._label_style(frame, button, on), justify="center")
            size = cell_len(button.label) + 4
            if button.kind == "ghost":
                cells.append(Padding(label, (1, 2)))
                continue
            cells.append(
                Panel(
                    label,
                    box=outline,
                    border_style=role_style(frame, "screen.focus" if on else "screen.border"),
                    width=size,
                    padding=(0, 1),
                )
            )
        grid.add_row(*cells)
        return grid

    def _plain(self, frame: Frame, focused: bool) -> RenderableType:
        line = Text()
        marker = frame.symbol("chosen")
        for index, button in enumerate(self.items):
            on = focused and index == self.focus
            if index:
                line.append("  ")
            line.append(
                f"{marker if on else ' ' * cell_len(marker)} {button.label}",
                style=self._label_style(frame, button, on),
            )
        return line
