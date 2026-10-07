"""Drive a screen without a terminal: feed keys, read back frames.

:func:`drive_screen` runs a :class:`~untaped.screen.core.Screen` on the same
:class:`~untaped.screen.runtime.Runtime` the terminal adapter uses, with a
host that runs commands synchronously after every key. It returns what the
user would see after each key as plain text, the final model and the outcome,
so a test asserts on behavior instead of on a terminal. No prompt_toolkit is
involved.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal, Self

from untaped.screen.core import (
    KEY_NAMES,
    Cancel,
    Cmd,
    Key,
    Paste,
    Quit,
    Resize,
    Screen,
    is_inline,
    is_key_name,
)
from untaped.screen.runtime import Runtime, SyncHost, capture_console
from untaped.stability import experimental
from untaped.theme import BUILTIN_THEMES, ThemeSpec

if TYPE_CHECKING:
    from rich.console import RenderableType

__all__ = ["ScreenKey", "ScreenKeys", "ScreenRun", "drive_screen", "rendered_text"]

type ScreenKey = str | Paste | Resize
"""A key name or single character, a paste, or a resize."""


@experimental
class ScreenKeys(tuple[ScreenKey, ...]):
    """A script of keys: what ``drive_screen`` feeds and a scripted backend replays."""

    def __new__(cls, *keys: ScreenKey) -> Self:
        return super().__new__(cls, keys)

    def __getnewargs__(self) -> tuple[ScreenKey, ...]:
        # Without this, copy and pickle call ``__new__`` with the tuple as one key.
        return tuple(self)


@experimental
@dataclass(frozen=True)
class ScreenRun[M, R]:
    """What a driven screen did.

    ``frames[0]`` is the screen before any key and ``frames[i]`` the one after
    key ``i``; ``frame`` is the last. ``result`` is the ``Quit`` result (or
    ``None``), ``outcome`` the ``Quit`` or ``Cancel`` that ended the screen
    (``None`` while it is still open) and ``commands_run`` the names of the
    commands that ran, in order.
    """

    result: R | None
    outcome: Quit[R] | Cancel | None
    model: M
    frames: tuple[str, ...]
    commands_run: tuple[str, ...]

    @property
    def frame(self) -> str:
        """The last frame."""
        return self.frames[-1]


def rendered_text(renderable: RenderableType, width: int, height: int) -> str:
    """``renderable`` as plain text on the screen console, trailing blanks trimmed."""
    console = capture_console(width, height, color_system=None, no_color=True)
    console.print(renderable, end="")
    text = console.file.getvalue()  # type: ignore[attr-defined]
    return "\n".join(line.rstrip() for line in text.removesuffix("\n").split("\n"))


@experimental
def drive_screen[M, R](
    screen: Screen[M, R],
    keys: Iterable[ScreenKey] = (),
    *,
    size: tuple[int, int] = (100, 30),
    theme: ThemeSpec = BUILTIN_THEMES["default"],
    commands: Literal["sync"] | Mapping[str, object | Callable[[], object]] = "sync",
) -> ScreenRun[M, R]:
    """Run ``screen`` with ``keys`` and return its frames, model and outcome.

    A key is a name from the shared table (``"up"``, ``"enter"``, ``"ctrl-s"``),
    a single character (a space is ``" "``), ``Paste("...")`` or ``Resize(w, h)``;
    any other string raises ``ValueError``, so a typo fails loudly. Commands run
    synchronously after each key, under the caller's context; ``commands`` as a
    mapping stubs a command by its ``Cmd.name`` (the value is the message, or a
    callable returning it) and every other command still runs; a stub name that
    matched no command raises ``ValueError`` once the run is over. Keys the
    screen gets after it ended are ignored.
    """
    messages = [_message(key) for key in keys]
    stubs = _stubs(commands)
    ran: list[str] = []
    host = SyncHost()
    runtime = Runtime(_instrumented(screen, stubs, ran), host, theme=theme, size=size)
    runtime.start()
    host.pump()
    frames = [_frame(runtime)]
    for message in messages:
        if not host.finished:
            runtime.send(message)
            host.pump()
        frames.append(_frame(runtime))
    unmatched = [name for name in stubs if name not in ran]
    if unmatched:
        raise ValueError(
            f"commands stubbed but never run: {', '.join(map(repr, unmatched))}; "
            f"commands that ran: {', '.join(map(repr, ran)) or 'none'}"
        )
    outcome = runtime.outcome
    return ScreenRun(
        result=outcome.result if isinstance(outcome, Quit) else None,
        outcome=outcome,
        model=runtime.model,
        frames=tuple(frames),
        commands_run=tuple(ran),
    )


def _frame[M, R](runtime: Runtime[M, R]) -> str:
    width, height = runtime.size
    return rendered_text(runtime.renderable(), width, height)


def _message(key: ScreenKey) -> object:
    if isinstance(key, Paste | Resize):
        return key
    if isinstance(key, str) and is_key_name(key):
        return Key(key)
    raise ValueError(
        f"unknown key {key!r}; use a key name ({', '.join(KEY_NAMES)}), a single character "
        "(a space is ' '), Paste(...) or Resize(...)"
    )


def _stubs(commands: object) -> Mapping[str, object | Callable[[], object]]:
    if commands == "sync":
        return {}
    if isinstance(commands, Mapping):
        return commands
    raise ValueError(f"commands must be 'sync' or a mapping of command names, not {commands!r}")


def _instrumented[M, R](
    screen: Screen[M, R], stubs: Mapping[str, object | Callable[[], object]], ran: list[str]
) -> Screen[M, R]:
    """``screen`` with every command recorded in ``ran`` and the stubbed ones replaced."""

    def wrap(cmd: Cmd) -> Cmd:
        if is_inline(cmd):
            return cmd

        def run() -> object:
            ran.append(cmd.name)
            if cmd.name in stubs:
                stub = stubs[cmd.name]
                return stub() if callable(stub) else stub
            return cmd.fn()

        return replace(cmd, fn=run)

    def init() -> tuple[M, list[Cmd]]:
        model, cmds = screen.init()
        return model, [wrap(cmd) for cmd in cmds]

    def update(model: M, message: object) -> tuple[M, list[Cmd]]:
        new_model, cmds = screen.update(model, message)
        return new_model, [wrap(cmd) for cmd in cmds]

    return replace(screen, init=init, update=update)
