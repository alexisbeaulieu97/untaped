"""The screen contract: what a screen is, the messages it receives, the keys it shares.

This module owns the vocabulary every screen, component and driver shares:
:class:`Screen` (three pure functions plus declarations), :class:`Cmd` (work
that comes back as a message), :class:`Binding` and the shared keys,
:class:`Frame` (the theme tokens a view draws with) and :class:`Footer`.
It never imports prompt_toolkit, and it imports Rich only inside the methods
that need it (:meth:`Frame.box`, :class:`Footer`), so loading it stays cheap;
``RenderableType`` is a type-checking import. The runtime that executes a
screen is :mod:`untaped.screen.runtime`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from untaped.stability import experimental
from untaped.theme import BUILTIN_THEMES, ROLE_NAMES, SYMBOL_NAMES, ThemeSpec

if TYPE_CHECKING:
    from rich.box import Box
    from rich.console import RenderableType

__all__ = [
    "KEY_NAMES",
    "SHARED_KEYS",
    "Activate",
    "Back",
    "Binding",
    "Cancel",
    "Cmd",
    "CmdError",
    "Frame",
    "Interrupt",
    "Key",
    "NextField",
    "Paste",
    "PrevField",
    "Quit",
    "Resize",
    "Screen",
    "SharedKey",
    "Submit",
    "is_inline",
]

#: Key names a ``Key`` message can carry besides a printable character (which is
#: its own name; the space bar is ``" "``).
KEY_NAMES: tuple[str, ...] = (
    "up",
    "down",
    "left",
    "right",
    "home",
    "end",
    "tab",
    "shift-tab",
    "enter",
    "esc",
    "backspace",
    "delete",
    "ctrl-u",
    "ctrl-w",
    "ctrl-s",
    "ctrl-c",
    "ctrl-r",
)


# --- commands -----------------------------------------------------------------


class _Send:
    """The function of an inline command: hands back its message without work."""

    def __init__(self, message: object) -> None:
        self.message = message

    def __call__(self) -> object:
        return self.message


@experimental
@dataclass(frozen=True)
class Cmd:
    """Work a screen asks the runtime to do; its return value comes back as a message.

    ``fn`` takes no arguments and returns a message (or ``None``). It runs off
    the event loop with the context captured when the command was issued, and
    an exception it raises becomes a :class:`CmdError`. A plain command is
    abandoned when the screen quits; ``write=True`` runs to completion even
    then; ``suspend=True`` runs with the screen left and the real terminal
    (``write`` and ``suspend`` together are refused). ``name`` labels the
    command in tests and logs and defaults to ``fn.__qualname__``.
    """

    fn: Callable[[], object | None]
    write: bool = False
    suspend: bool = False
    name: str = ""

    def __post_init__(self) -> None:
        if self.write and self.suspend:
            raise ValueError("a command cannot be both write=True and suspend=True")
        if not self.name:
            label = getattr(self.fn, "__qualname__", None) or type(self.fn).__name__
            object.__setattr__(self, "name", label)

    @staticmethod
    def send(message: object) -> Cmd:
        """A command that delivers ``message`` in the same loop turn, with no thread."""
        return Cmd(_Send(message), name="send")


def is_inline(cmd: Cmd) -> bool:
    """Whether ``cmd`` came from :meth:`Cmd.send`, which the runtime delivers inline."""
    return isinstance(cmd.fn, _Send)


# --- messages -----------------------------------------------------------------


@experimental
@dataclass(frozen=True)
class Key:
    """A key press, by name (see :data:`KEY_NAMES`; a printable character is its own name)."""

    name: str


@experimental
@dataclass(frozen=True)
class Paste:
    """Text pasted into the terminal, delivered as one message."""

    text: str


@experimental
@dataclass(frozen=True)
class Resize:
    """The terminal's new size in cells."""

    width: int
    height: int


@experimental
@dataclass(frozen=True)
class CmdError:
    """A command raised ``error``; the screen decides how to show it."""

    error: BaseException


@experimental
@dataclass(frozen=True)
class Quit[R]:
    """Ends the screen with ``result``, the value ``UiContext.run`` returns.

    The runtime consumes it; it never reaches ``update``. A screen sends it by
    returning ``Cmd.send(Quit(result))``.
    """

    result: R


@experimental
@dataclass(frozen=True)
class Cancel:
    """Ends the screen without a result (``interrupted`` for an interrupt, exit 130).

    Consumed by the runtime like :class:`Quit`.
    """

    interrupted: bool = False


@experimental
@dataclass(frozen=True)
class Back:
    """Esc after the focused component and the screen's bindings passed on it."""


@experimental
@dataclass(frozen=True)
class Interrupt:
    """Ctrl-c after the focused component and the screen's bindings passed on it."""


@experimental
@dataclass(frozen=True)
class NextField:
    """Tab after the focused component passed on it."""


@experimental
@dataclass(frozen=True)
class PrevField:
    """Shift-tab after the focused component passed on it."""


@experimental
@dataclass(frozen=True)
class Activate:
    """Enter after the focused component and the screen's bindings passed on it."""


@experimental
@dataclass(frozen=True)
class Submit:
    """Ctrl-s after the focused component and the screen's bindings passed on it."""


# --- shared keys --------------------------------------------------------------


@dataclass(frozen=True)
class SharedKey:
    """One row of the shared-key table.

    ``message`` is delivered to ``update`` after the focused component and the
    screen's bindings passed on the key (``None``: the runtime owns the key);
    ``unhandled`` is what happens when ``update`` ignored that message too
    (``None``: nothing).
    """

    key: str
    label: str
    message: object | None
    unhandled: object | None = None


#: The keys every screen shares, in the order the footer and help overlay show them.
SHARED_KEYS: dict[str, SharedKey] = {
    shared.key: shared
    for shared in (
        SharedKey("esc", "back", Back(), Cancel()),
        SharedKey("ctrl-c", "quit", Interrupt(), Cancel(interrupted=True)),
        SharedKey("tab", "next field", NextField()),
        SharedKey("shift-tab", "previous field", PrevField()),
        SharedKey("enter", "activate", Activate()),
        SharedKey("ctrl-s", "submit", Submit()),
        SharedKey("?", "help", None),
    )
}


# --- bindings, frame, screen -------------------------------------------------


@experimental
@dataclass(frozen=True)
class Binding:
    """A screen's own key: ``message`` goes to ``update`` when nothing else took the key.

    ``label`` is the footer text. ``message=None`` is a footer-only entry that
    documents a key a component handles. ``when`` limits the binding (and its
    footer entry) to models for which it returns true. ``key`` is never one of
    :data:`SHARED_KEYS`; :class:`Screen` refuses that.
    """

    key: str
    label: str
    message: object | None
    when: Callable[[Any], bool] | None = None


@experimental
@dataclass(frozen=True)
class Frame:
    """What a view draws with: the size it has and the theme's tokens.

    ``height`` is the rows left for the view (the footer is already taken off).
    Views and components take every glyph, style and box from here, never a
    literal, so a theme change reaches every screen.
    """

    width: int
    height: int
    theme: ThemeSpec

    def symbol(self, name: str) -> str:
        """The theme's glyph for ``name``, or the default theme's when it defines none."""
        if name not in SYMBOL_NAMES:
            raise KeyError(f"unknown symbol {name!r}; declared symbols: {', '.join(SYMBOL_NAMES)}")
        if name in self.theme.symbols:
            return self.theme.symbols[name]
        return BUILTIN_THEMES["default"].symbols.get(name, "")

    def style(self, role: str) -> str:
        """The Rich style of the colour role ``role`` (empty when no theme styles it)."""
        if role not in ROLE_NAMES:
            raise KeyError(f"unknown color role {role!r}; declared roles: {', '.join(ROLE_NAMES)}")
        if role in self.theme.color_roles:
            return self.theme.color_roles[role]
        return BUILTIN_THEMES["default"].color_roles.get(role, "")

    def box(self) -> Box | None:
        """The Rich box of the theme's border style; ``None`` when the border is ``none``."""
        from untaped.render import resolve_box  # noqa: PLC0415 - keeps Rich out of import time

        return resolve_box(self.theme.border)

    def ellipsis(self) -> str:
        """The token that marks cut text."""
        return self.symbol("ellipsis")


@experimental
@dataclass(frozen=True)
class Screen[M, R]:
    """An interactive terminal UI: three pure functions and what the runtime needs to know.

    ``init`` returns the first model and its commands; ``update`` takes the
    model and a message and returns the next model and commands; ``view`` draws
    the model as a Rich renderable (a plain ``str`` is literal text). A screen
    ends by returning ``Cmd.send(Quit(result))`` or ``Cmd.send(Cancel())``.

    ``command`` and ``alternative`` are required: when there is no terminal the
    runtime fails with ``command`` needs a terminal; use ``alternative``.
    ``keys`` are the screen's own bindings; the shared keys (:data:`SHARED_KEYS`)
    belong to the SDK and cannot be rebound. ``layout`` is ``"full"`` (the
    whole terminal) or ``"inline"`` (below the cursor, erased when done).
    """

    init: Callable[[], tuple[M, Sequence[Cmd]]]
    update: Callable[[M, object], tuple[M, Sequence[Cmd]]]
    view: Callable[[M, Frame], RenderableType]
    title: str
    command: str
    alternative: str
    keys: tuple[Binding, ...] = ()
    layout: Literal["full", "inline"] = "full"

    def __post_init__(self) -> None:
        if not self.command.strip():
            raise ValueError("a screen needs a command: the one it shows when there is no terminal")
        if not self.alternative.strip():
            raise ValueError(
                "a screen needs an alternative: the non-interactive way to do what it does"
            )
        if self.layout not in ("full", "inline"):
            raise ValueError(f"layout must be 'full' or 'inline', not {self.layout!r}")
        for binding in self.keys:
            if binding.key in SHARED_KEYS:
                raise ValueError(
                    f"key {binding.key!r} is shared by every screen and cannot be bound; "
                    f"shared keys: {', '.join(SHARED_KEYS)}"
                )
