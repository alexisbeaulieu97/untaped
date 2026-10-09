"""Test helpers for the components: a one-component screen and frame readers."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from rich.console import Console, RenderableType
from rich.segment import Segment
from rich.style import Style

from untaped.screen.components.draw import role_style
from untaped.screen.components.fields import Field
from untaped.screen.core import (
    Activate,
    Back,
    Cmd,
    Frame,
    Interrupt,
    Key,
    NextField,
    Paste,
    PrevField,
    Resize,
    Screen,
    Submit,
)
from untaped.testing import ScreenRun, drive_screen
from untaped.theme import BUILTIN_THEMES, ThemeSpec


@dataclass(frozen=True)
class Solo:
    """The model of :func:`solo`: the component and what it did not consume."""

    field: Any
    unhandled: tuple[object, ...] = ()
    seen: tuple[object, ...] = ()


def solo(component: Field, *, focused: bool = True, width: int | None = None) -> Screen[Solo, str]:
    """A screen that is only ``component``: every message goes to it, focused.

    ``Activate``, ``Submit`` and the focus messages that reach ``update`` after
    the component passed on them are counted in ``unhandled`` (what a form would
    act on). ``Back`` and ``Interrupt`` are left alone so the runtime still ends the
    screen with ``Cancel`` when the component ignores them. Any other message the
    component sent itself (``Pressed``) is recorded in ``seen``.
    """

    def update(model: Solo, message: object) -> tuple[Solo, list[Cmd]]:
        field, cmds = model.field.update(message)
        if field is model.field and not cmds:
            if isinstance(message, Activate | Submit | NextField | PrevField):
                return replace(model, unhandled=(*model.unhandled, message)), []
            if isinstance(message, Key | Paste | Resize | Back | Interrupt):
                return model, []
            return replace(model, seen=(*model.seen, message)), []
        return replace(model, field=field), list(cmds)

    def view(model: Solo, frame: Frame) -> RenderableType:
        return model.field.view(frame, focused=focused, width=width)

    return Screen(
        init=lambda: (Solo(component), []),
        update=update,
        view=view,
        title="Solo",
        command="untaped solo",
        alternative="untaped solo --flag",
    )


def run_solo(
    component: Field,
    *keys: Any,
    theme: ThemeSpec = BUILTIN_THEMES["default"],
    size: tuple[int, int] = (60, 20),
    focused: bool = True,
    width: int | None = None,
) -> ScreenRun[Solo, str]:
    """Drive :func:`solo` of ``component`` with ``keys``."""
    return drive_screen(solo(component, focused=focused, width=width), keys, size=size, theme=theme)


def field_of(run: ScreenRun[Solo, str]) -> Any:
    """The component after the run."""
    return run.model.field


def lines(frame: str) -> list[str]:
    """The non-empty lines of a frame, the footer excluded."""
    return [line for line in frame.splitlines() if line.strip()][:-1]


def render_styled(
    renderable: RenderableType, *, width: int = 60, theme: ThemeSpec | None = None
) -> list[Segment]:
    """``renderable`` as Rich segments on a truecolor console, for asserting on styles."""
    console = Console(
        width=width,
        force_terminal=True,
        color_system="truecolor",
        markup=False,
        emoji=False,
        highlight=False,
        legacy_windows=False,
    )
    return list(console.render(renderable))


def style_of(segments: Sequence[Segment], needle: str) -> Style:
    """The style of the first segment whose text contains ``needle``."""
    for segment in segments:
        if needle in segment.text and segment.style is not None:
            return segment.style
    raise AssertionError(f"no segment contains {needle!r}: {[s.text for s in segments]}")


def role(theme: ThemeSpec, name: str) -> Style:
    """The Rich style of screen role ``name`` in ``theme``."""
    return role_style(Frame(80, 24, theme), name)


def gallery_fields() -> tuple[tuple[str, Any, str], ...]:
    """Every component, as ``(name, component, text that shows while it has focus)``."""
    from pydantic import SecretStr

    from untaped.screen.components.buttons import Button, Buttons
    from untaped.screen.components.choices import (
        Check,
        Cycle,
        ListItem,
        MultiList,
        Select,
        SingleList,
    )
    from untaped.screen.components.form import Form
    from untaped.screen.components.inputs import NumberInput, PathInput, SecretInput, TextInput
    from untaped.screen.components.layout import Panes
    from untaped.screen.components.lists import SearchList, Tags, Tree, TreeRow
    from untaped.screen.components.tabs import Tab, Tabs

    names = tuple(ListItem(name, name) for name in ("awx", "jira", "github", "ansible"))
    many = tuple(ListItem(f"n{n}", f"item {n:03d}") for n in range(300))
    caps = SingleList("", names, "github", show_chosen=False)
    inner = Form(
        (("url", TextInput("Pane URL", "https://x.test")), ("note", TextInput("Pane note")))
    )
    rows = (
        TreeRow("all", "all items", "3 set", (TreeRow("all.branch", "branch", "main"),)),
        TreeRow("api", "acme/api", "inherit"),
    )
    return (
        ("text", TextInput("Base URL", "https://x.test", help="The address."), "Base URL"),
        ("path", PathInput("Path", "/no/such/gallery/dir"), "Path"),
        ("secret", SecretInput("Token", SecretStr("")), "Token"),
        ("number", NumberInput("Timeout", "30", minimum=1, maximum=600), "Timeout"),
        ("check", Check("Verify TLS", True), "Verify TLS"),
        ("select", Select("Region", names, "jira"), "Region"),
        ("single", SingleList("Output", names, "awx"), "Output"),
        ("multi", MultiList("Services", names, frozenset({"awx"})), "Services"),
        ("cycle", Cycle("Mode", ("a", "b", "c"), "b"), "Mode"),
        (
            "tabs",
            Tabs(
                "Token source",
                (
                    Tab("keychain", "Keychain", (("token", SecretInput("Stored token")),)),
                    Tab("command", "Command", (("command", TextInput("Run command")),)),
                ),
            ),
            "Token source",
        ),
        ("search", SearchList("Repos", many, multi=True, note="refreshed"), "Repos"),
        ("tags", Tags("Plugins", names, ("awx",)), "Plugins"),
        ("tree", Tree("Settings", rows, frozenset({"all"})), "Settings"),
        (
            "buttons",
            Buttons((Button("save", "Save", "primary"), Button("cancel", "Cancel", "ghost"))),
            "Cancel",
        ),
        # last: Panes wraps focus between its two panes, so a tab never leaves it
        (
            "panes",
            Panes(caps, inner, left_title="Left pane", right_title="Right pane"),
            "Left pane",
        ),
    )
