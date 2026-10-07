"""The terminal adapter: the one module that imports prompt_toolkit.

It runs a screen's :class:`~untaped.screen.runtime.Runtime` in a
prompt_toolkit ``Application``: key presses become ``Key`` and ``Paste``
messages, the runtime's Rich renderable is painted through one capture console
(ANSI in a ``FormattedTextControl``), and commands run on threads that post
their messages back to the event loop. Nothing else in the workspace may
import prompt_toolkit (``tests/repo/test_terminal_boundary.py`` and the
``terminal-boundary`` convention check), so a different terminal library can
replace this module without touching a screen.

The adapter enters the alternate screen only for a ``full`` layout; an
``inline`` screen draws below the cursor and erases itself when done. Colour is
decided once (:func:`~untaped.screen.color.detect_color`) and passed to both
Rich and ``Application(color_depth=...)``. A ``suspend`` command leaves the
screen through ``run_in_terminal``, so the command gets the real terminal.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import os
import threading
from collections.abc import Callable, Mapping
from typing import Any

from prompt_toolkit.application import Application, run_in_terminal
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.input import Input
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.output import ColorDepth, Output

from untaped.screen.color import ColorMode, detect_color, ptk_depth_name, rich_system
from untaped.screen.core import Cancel, Key, Paste, Quit, Resize, Screen
from untaped.screen.runtime import CmdKind, Runtime, capture_console
from untaped.theme import ThemeSpec

__all__ = ["TerminalHost", "build_application", "run_terminal_screen"]

type Outcome[R] = Quit[R] | Cancel

#: prompt_toolkit key name -> the name a ``Key`` message carries.
_KEYS: dict[str, str] = {
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
    "home": "home",
    "end": "end",
    "tab": "tab",
    "s-tab": "shift-tab",
    "enter": "enter",
    "escape": "esc",
    "backspace": "backspace",
    "delete": "delete",
    "c-u": "ctrl-u",
    "c-w": "ctrl-w",
    "c-s": "ctrl-s",
    "c-c": "ctrl-c",
    "c-r": "ctrl-r",
}

#: How long prompt_toolkit waits to tell a lone escape key from an escape sequence.
_ESCAPE_TIMEOUT = 0.05


class TerminalHost:
    """The :class:`~untaped.screen.runtime.Host` for a prompt_toolkit application.

    Background and write commands run on their own threads (a write thread is
    not a daemon, so the interpreter cannot kill it half-way); a suspend
    command runs through ``run_in_terminal``. Calls from those threads reach
    the loop thread through ``call_soon_threadsafe``. :func:`build_application`
    attaches ``app`` and ``apply``; the loop is captured when the app starts.
    """

    def __init__(self) -> None:
        self.app: Application[Any] | None = None
        self.apply: Callable[[Callable[[], None]], None] = lambda call: call()
        self.outcome: Callable[[], object] = lambda: None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._context: contextvars.Context | None = None
        self._finished = False

    def bind_loop(self) -> None:
        """Remember the running loop and the application's context (call from ``pre_run``)."""
        self._loop = asyncio.get_running_loop()
        self._context = self.app.context if self.app is not None else None

    def spawn(self, job: Callable[[], None], *, kind: CmdKind) -> None:
        if kind == "suspend":
            self._call_soon(lambda: asyncio.ensure_future(run_in_terminal(job, in_executor=True)))
            return
        threading.Thread(
            target=job, daemon=kind == "background", name=f"untaped-screen-{kind}"
        ).start()

    def post(self, call: Callable[[], None]) -> None:
        self._call_soon(lambda: self.apply(call))

    def redraw(self) -> None:
        if self.app is not None:
            self.app.invalidate()

    def finish(self) -> None:
        if self._finished or self.app is None or self.app.is_done:
            return
        self._finished = True
        self.app.exit(result=self.outcome())

    def _call_soon(self, call: Callable[[], object]) -> None:
        loop = self._loop
        if loop is None:
            return
        # The loop is closed once the screen ended; a late result has nowhere to go.
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(call, context=self._context)


def build_application[M, R](
    screen: Screen[M, R],
    *,
    input: Input,
    output: Output,
    theme: ThemeSpec,
    environ: Mapping[str, str] | None = None,
) -> tuple[Application[Outcome[R]], TerminalHost, Runtime[M, R]]:
    """The application, its host and its runtime for ``screen``, not yet running.

    Split from :func:`run_terminal_screen` so tests can look at what was built
    (the colour depth, the layout) and drive the application themselves.
    """
    mode = detect_color(os.environ if environ is None else environ)
    host = TerminalHost()
    size = output.get_size()
    runtime = Runtime(screen, host, theme=theme, size=(size.columns, size.rows))
    host.outcome = lambda: runtime.outcome

    def fail(error: BaseException) -> None:
        # A screen that raises must not leave prompt_toolkit's "press ENTER" handler in charge.
        if not application.is_done:
            application.exit(exception=error)

    def apply(call: Callable[[], None]) -> None:
        try:
            call()
        except Exception as error:
            fail(error)

    def send(message: object) -> None:
        apply(lambda: runtime.send(message))

    def draw() -> Any:
        try:
            return _paint(runtime, application.output, mode, send)
        except Exception as error:
            fail(error)
            return ""

    control = FormattedTextControl(draw, focusable=True, show_cursor=False)
    inline = screen.layout == "inline"
    application: Application[Outcome[R]] = Application(
        layout=Layout(Window(control, dont_extend_height=inline, always_hide_cursor=True)),
        key_bindings=_bindings(on_sigint=lambda: _interrupt(application), send=send),
        full_screen=not inline,
        erase_when_done=inline,
        mouse_support=False,
        color_depth=ColorDepth[ptk_depth_name(mode)],
        input=input,
        output=output,
    )
    application.ttimeoutlen = (
        _ESCAPE_TIMEOUT  # esc reacts at once instead of waiting for a sequence
    )
    host.app = application
    host.apply = apply
    return application, host, runtime


def run_terminal_screen[M, R](
    screen: Screen[M, R],
    *,
    input: Input,
    output: Output,
    theme: ThemeSpec,
    environ: Mapping[str, str] | None = None,
) -> Outcome[R]:
    """Run ``screen`` on ``input`` and ``output`` and return how it ended.

    Raises ``KeyboardInterrupt`` for an external SIGINT, ``EOFError`` when the
    input closes, and whatever a screen's own code raised.
    """
    application, host, runtime = build_application(
        screen, input=input, output=output, theme=theme, environ=environ
    )

    def start() -> None:
        host.bind_loop()
        host.apply(runtime.start)

    return application.run(pre_run=start)


def _paint[M, R](
    runtime: Runtime[M, R], output: Output, mode: ColorMode, send: Callable[[object], None]
) -> ANSI:
    """The runtime's frame as ANSI at the terminal's current size."""
    terminal = output.get_size()
    size = (terminal.columns, terminal.rows)
    if size != runtime.size:
        # Draw at the new size now; the message tells the screen once draw is over.
        runtime.size = size
        asyncio.get_running_loop().call_soon(send, Resize(*size))
    console = capture_console(
        size[0], size[1], color_system=rich_system(mode), no_color=mode.no_color
    )
    console.print(runtime.renderable(), end="")
    text: str = console.file.getvalue()  # type: ignore[attr-defined]
    return ANSI(text)


def _interrupt(application: Application[Any]) -> None:
    """An external SIGINT (``kill -INT``): the ctrl-c *key* is a ``Key`` message instead."""
    if not application.is_done:
        application.exit(exception=KeyboardInterrupt())


def _bindings(*, on_sigint: Callable[[], None], send: Callable[[object], None]) -> KeyBindings:
    bindings = KeyBindings()
    for ptk_key, name in _KEYS.items():
        bindings.add(ptk_key, eager=True)(_named(send, name))

    def feed(text: str) -> None:
        for char in text:
            if char.isprintable():
                send(Key(char))

    @bindings.add(Keys.SIGINT)
    def _interrupted(_event: KeyPressEvent) -> None:
        on_sigint()

    @bindings.add(Keys.BracketedPaste)
    def _pasted(event: KeyPressEvent) -> None:
        text = "".join(char for char in event.data if char.isprintable())
        if text:
            send(Paste(text))

    @bindings.add(Keys.Any)
    def _typed(event: KeyPressEvent) -> None:
        if isinstance(event.key_sequence[0].key, Keys):
            return  # tail of an unbound escape sequence (F1, ...), not typed text
        feed(event.data)

    return bindings


def _named(send: Callable[[object], None], name: str) -> Callable[[KeyPressEvent], None]:
    def handler(_event: KeyPressEvent) -> None:
        send(Key(name))

    return handler
