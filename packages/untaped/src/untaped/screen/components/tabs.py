"""``Tabs``: underline tabs whose active tab shows its own fields.

One tab strip in a labelled box. The header (the tab names over an underline)
is the first focus slot; the active tab's fields follow, each drawn without a
box of its own (label line, value, help) inside the strip's. ``left`` and
``right`` change the tab while the header has focus; the focus messages move
through the header and the active tab's fields, and at either end the strip
returns itself so the parent (a form, the screen) moves on. A key the focused
field consumes never reaches the strip's own handling, so a completion list or
an open ``Select`` inside a tab keeps tab and esc to itself.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Self

from rich.cells import cell_len
from rich.console import Group, RenderableType
from rich.text import Text

from untaped.screen.components.box import field_box
from untaped.screen.components.draw import BOLD, inner_width, role_style
from untaped.screen.components.fields import Field
from untaped.screen.core import Cmd, Frame, Key, NextField, PrevField
from untaped.screen.fit import fit_text
from untaped.stability import experimental

__all__ = ["Tab", "Tabs"]


@experimental
@dataclass(frozen=True)
class Tab:
    """One tab: its ``id`` (what ``Tabs.value`` reports), the ``label`` shown in the header,
    its named ``fields`` and a muted ``note`` shown below them."""

    id: str
    label: str
    fields: tuple[tuple[str, Field], ...] = ()
    note: str = ""


@experimental
@dataclass(frozen=True)
class Tabs:
    """A labelled box of tabs; ``active`` is the shown tab's ``id`` (the first when unset).

    ``focus`` is the slot that has focus: ``0`` is the header, ``1`` and up the
    active tab's fields in order. ``value`` is ``{"tab": id, **fields}`` for the
    active tab's fields only, and :meth:`validate` checks only those; the
    fields of the other tabs keep what they hold while hidden.
    """

    label: str
    tabs: tuple[Tab, ...]
    active: str = ""
    focus: int = 0
    help: str = ""
    error: str = ""

    def __post_init__(self) -> None:
        if not self.tabs:
            raise ValueError("Tabs needs at least one tab")
        ids = [tab.id for tab in self.tabs]
        if self.active not in ids:
            object.__setattr__(self, "active", ids[0])
        object.__setattr__(self, "focus", max(0, min(self.focus, len(self.current.fields))))

    @property
    def current(self) -> Tab:
        """The active tab."""
        return next(tab for tab in self.tabs if tab.id == self.active)

    @property
    def value(self) -> dict[str, object]:
        """``{"tab": id}`` plus each field of the active tab by name."""
        return {"tab": self.active, **{name: field.value for name, field in self.current.fields}}

    def with_error(self, text: str) -> Self:
        """This strip showing ``text`` as its own error (empty clears it)."""
        return replace(self, error=text)

    def validate(self) -> str:
        """The first error among the active tab's fields (empty when they are all fine)."""
        return next((error for _, field in self.current.fields if (error := field.validate())), "")

    def update(self, message: object) -> tuple[Self, list[Cmd]]:
        """Switch tabs on left or right in the header, hand anything else to the focused field.

        A focus message the field passed on moves between the header and the
        fields; past the last field or before the header the strip is returned
        unchanged so the parent takes the move.
        """
        fields = self.current.fields
        if self.focus == 0:
            if isinstance(message, Key) and message.name in ("left", "right"):
                return self._switched(1 if message.name == "right" else -1), []
            if isinstance(message, NextField) and fields:
                return replace(self, focus=1), []
            return self, []
        slot = self.focus - 1
        _, field = fields[slot]
        updated, cmds = field.update(message)
        if updated is not field or cmds:
            return self._with_field(slot, updated, changed=updated.value != field.value), list(cmds)
        if isinstance(message, NextField) and self.focus < len(fields):
            return replace(self, focus=self.focus + 1), []
        if isinstance(message, PrevField):
            return replace(self, focus=self.focus - 1), []
        return self, []

    def _switched(self, step: int) -> Self:
        ids = [tab.id for tab in self.tabs]
        index = max(0, min(ids.index(self.active) + step, len(ids) - 1))
        if ids[index] == self.active:
            return self
        return replace(self, active=ids[index], error="")

    def _with_field(self, slot: int, updated: Field, *, changed: bool) -> Self:
        tab = self.current
        fields = tuple(
            (name, updated if index == slot else field)
            for index, (name, field) in enumerate(tab.fields)
        )
        tabs = tuple(
            replace(item, fields=fields) if item.id == tab.id else item for item in self.tabs
        )
        return replace(self, tabs=tabs, error="" if changed else self.error)

    def view(
        self, frame: Frame, *, focused: bool = False, width: int | None = None
    ) -> RenderableType:
        """The box: the header, a blank line, the active tab's fields and its note."""
        inner = inner_width(frame, width)
        body: list[RenderableType] = [
            *_header(frame, inner, self.tabs, self.active, focused and self.focus == 0),
            Text(""),
        ]
        bare = replace(frame, theme=frame.theme.model_copy(update={"border": "none"}))
        for slot, (_, field) in enumerate(self.current.fields, start=1):
            body.append(field.view(bare, focused=focused and self.focus == slot, width=inner))
        if self.current.note:
            body.append(Text(self.current.note, style=role_style(frame, "screen.muted")))
        return field_box(
            frame, self.label, Group(*body), focused=focused, error=self.error, help=self.help,
            width=width,
        )  # fmt: skip


def _header(
    frame: Frame, inner: int, tabs: tuple[Tab, ...], active: str, focused: bool
) -> list[Text]:
    """The tab names centred over their cells and the underline, bright under the active one."""
    cell = max(1, inner // len(tabs))
    names, rule = Text(), Text()
    for index, tab in enumerate(tabs):
        size = cell if index < len(tabs) - 1 else max(1, inner - cell * (len(tabs) - 1))
        on = tab.id == active
        if on:
            style = role_style(frame, "screen.accent" if focused else "screen.value") + BOLD
        else:
            style = role_style(frame, "screen.muted")
        label = Text(" " * max(0, (size - cell_len(tab.label)) // 2))
        label.append(tab.label, style=style)
        label = fit_text(label, size, frame.ellipsis())
        names.append_text(label)
        glyph = frame.symbol("tab.active" if on else "tab.inactive")
        rule.append(
            glyph * size, style=role_style(frame, "screen.accent" if on else "screen.border")
        )
    return [names, rule]
