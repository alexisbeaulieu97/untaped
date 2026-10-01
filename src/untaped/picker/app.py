"""prompt_toolkit front end for the picker: one inline window redrawn from state.

The only picker module that imports prompt_toolkit; ``untaped.prompts``
imports it lazily. Key presses become :func:`untaped.picker.state.handle`
calls; the catalog refresh runs on a daemon thread so quitting never waits for
the network, and its result is applied on the event loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Callable

from prompt_toolkit.application import Application
from prompt_toolkit.input import Input
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.output import Output
from prompt_toolkit.styles import BaseStyle, Style, merge_styles

from untaped.picker import PickCatalog, PickRequest, PickResult
from untaped.picker.state import (
    PickerState,
    begin_refresh,
    handle,
    initial_state,
    refresh_failed,
    result,
    with_catalog,
)
from untaped.picker.view import render

DEFAULT_STYLE: dict[str, str] = {
    "picker.heading": "bold",
    "picker.subtitle": "ansicyan",
    "picker.border": "ansibrightblack",
    "picker.border.focus": "ansicyan",
    "picker.cursor": "ansicyan bold",
    "picker.mark": "ansicyan",
    "picker.match": "bold underline",
    "picker.dim": "ansibrightblack",
    "picker.value": "",
    "picker.error": "ansired",
    "picker.button": "",
    "picker.button.focus": "reverse bold",
    "picker.keys": "ansibrightblack",
}
"""Muted palette with one accent; the theme's prompt style is merged on top."""

_KEYS: dict[str, str] = {
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
    "tab": "tab",
    "enter": "enter",
    "escape": "esc",
    "backspace": "backspace",
    "delete": "delete",
    "c-u": "ctrl-u",
    "c-w": "ctrl-w",
    "c-s": "ctrl-s",
    "c-c": "ctrl-c",
}
"""prompt_toolkit key → :func:`untaped.picker.state.handle` key name."""


def _daemon(work: Callable[[], None]) -> None:
    threading.Thread(target=work, daemon=True, name="untaped-picker-refresh").start()


def _fetch(
    source: Callable[[bool], PickCatalog], force: bool
) -> Callable[[PickerState], PickerState]:
    """Call the refresh source; return the state update for its outcome (never raises)."""
    try:
        catalog = source(force)
    except Exception as exc:  # the picker stays usable on the old catalog
        message = str(exc) or type(exc).__name__
        return lambda state: refresh_failed(state, message)
    return lambda state: with_catalog(state, catalog)


def run_picker(
    request: PickRequest,
    *,
    input: Input,
    output: Output,
    style: BaseStyle | None = None,
    spawn: Callable[[Callable[[], None]], None] | None = None,
) -> PickResult | None:
    """Run the picker inline and return the confirmed result, or ``None`` if cancelled."""
    box: list[PickerState] = [initial_state(request)]
    start = spawn or _daemon

    def settle() -> None:
        if app.is_done:
            return
        if box[0].outcome == "confirmed":
            app.exit(result=result(box[0]))
        elif box[0].outcome == "cancelled":
            app.exit(result=None)

    def apply(key: str) -> None:
        box[0] = handle(box[0], key)
        settle()

    def refresh(force: bool) -> None:
        source = request.refresh
        if source is None or box[0].refreshing:
            return
        box[0] = begin_refresh(box[0])
        loop = asyncio.get_running_loop()

        def work() -> None:
            update = _fetch(source, force)

            def land() -> None:
                box[0] = update(box[0])
                app.invalidate()

            with contextlib.suppress(RuntimeError):  # the picker already closed
                loop.call_soon_threadsafe(land)

        start(work)

    def draw() -> list[tuple[str, str]]:
        size = app.output.get_size()
        return render(box[0], size.columns, size.rows)

    control = FormattedTextControl(draw, focusable=True, show_cursor=False)
    app: Application[PickResult | None] = Application(
        layout=Layout(Window(control, dont_extend_height=True, always_hide_cursor=True)),
        key_bindings=_bindings(apply, refresh),
        style=merge_styles([Style.from_dict(DEFAULT_STYLE), style or Style([])]),
        full_screen=False,
        erase_when_done=True,
        mouse_support=False,
        input=input,
        output=output,
    )
    app.ttimeoutlen = 0.05  # esc reacts at once instead of waiting for a sequence
    return app.run(pre_run=lambda: refresh(False))


def _bindings(apply: Callable[[str], None], refresh: Callable[[bool], None]) -> KeyBindings:
    bindings = KeyBindings()
    for ptk_key, name in _KEYS.items():
        bindings.add(ptk_key)(_bound(apply, name))

    @bindings.add("c-r")
    def _refresh(_event: KeyPressEvent) -> None:
        refresh(True)

    @bindings.add(Keys.SIGINT)
    def _interrupted(event: KeyPressEvent) -> None:
        event.app.exit(exception=KeyboardInterrupt())

    def feed(text: str) -> None:
        for char in text:
            if char.isprintable():
                apply(char)

    @bindings.add(Keys.BracketedPaste)
    def _pasted(event: KeyPressEvent) -> None:
        feed(event.data)

    @bindings.add(Keys.Any)
    def _typed(event: KeyPressEvent) -> None:
        if isinstance(event.key_sequence[0].key, Keys):
            return  # tail of an unbound escape sequence (Home, F1, ...), not typed text
        feed(event.data)

    return bindings


def _bound(apply: Callable[[str], None], name: str) -> Callable[[KeyPressEvent], None]:
    def handler(_event: KeyPressEvent) -> None:
        apply(name)

    return handler
