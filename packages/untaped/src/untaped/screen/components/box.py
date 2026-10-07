"""The labelled box every field is drawn in.

:func:`field_box` is the one place that frames a field: the label sits in the
top border, the body is inside, and a muted help line or a red error line
follows. With the theme's border set to ``none`` the box goes away and the
field is its label line, its body lines and its help line.
"""

from __future__ import annotations

from rich.console import Group, RenderableType
from rich.constrain import Constrain
from rich.panel import Panel
from rich.text import Text

from untaped.screen.components.draw import role_style
from untaped.screen.core import Frame

__all__ = ["field_box"]


def field_box(
    frame: Frame,
    label: str,
    body: RenderableType,
    *,
    focused: bool = False,
    error: str = "",
    help: str = "",
    width: int | None = None,
) -> RenderableType:
    """``body`` in a box ``width`` cells wide (the frame's width by default), labelled ``label``.

    The border is ``screen.error`` while there is an error, ``screen.focus``
    while focused and ``screen.border`` otherwise; the label is bright and bold
    when focused or in error. The error replaces the help under the box.
    """
    total = width or frame.width
    note = _note(frame, error, help)
    outline = frame.box()
    if outline is None:
        title = _plain_label(frame, label, focused, error)
        lines: list[RenderableType] = [*([title] if title else []), body]
        if note is not None:
            lines.append(Constrain(note, total))
        return Group(*lines)
    if error:
        border = role_style(frame, "screen.error")
    elif focused:
        border = role_style(frame, "screen.focus")
    else:
        border = role_style(frame, "screen.border")
    title_style = role_style(frame, "screen.accent" if focused or error else "screen.value")
    panel = Panel(
        body,
        title=Text(label, style=title_style) if label else None,
        title_align="left",
        box=outline,
        border_style=border,
        width=total,
        padding=(0, 1),
    )
    # A ``Group`` keeps the panel at the height of its body; a bare ``Panel`` would
    # stretch to whatever height the parent's layout offers.
    return Group(panel, Constrain(note, total)) if note is not None else Group(panel)


def _note(frame: Frame, error: str, help: str) -> Text | None:
    if error:
        return Text(error, style=role_style(frame, "screen.error"))
    if help:
        return Text(help, style=role_style(frame, "screen.muted"))
    return None


def _plain_label(frame: Frame, label: str, focused: bool, error: str) -> Text | None:
    if not label:
        return None
    if error:
        role = "screen.error"
    elif focused:
        role = "screen.accent"
    else:
        role = "screen.muted"
    return Text(label, style=role_style(frame, role))
