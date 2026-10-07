"""The terminal adapter against real prompt_toolkit, with pipe input and recorded output."""

from __future__ import annotations

import asyncio
import io
import os
import signal
import sys
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace

import pytest
from prompt_toolkit.data_structures import Size
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.input import PipeInput, create_pipe_input
from prompt_toolkit.output import ColorDepth, DummyOutput, Output
from prompt_toolkit.output.vt100 import Vt100_Output

from untaped.screen.core import Cancel, Cmd, Frame, Key, Paste, Quit, Resize, Screen
from untaped.screen.terminal import TerminalHost, build_application, run_terminal_screen
from untaped.theme import BUILTIN_THEMES

THEME = BUILTIN_THEMES["default"]
ENTER = "\r"
ESC = "\x1b"
REACTION_LIMIT = 0.4  # a held-back key takes half a second or more; a normal reaction ~0.06
F1 = "\x1bOP"  # an escape sequence no screen binds
ALT_SCREEN_ON = "\x1b[?1049h"
ALT_SCREEN_OFF = "\x1b[?1049l"


@dataclass(frozen=True)
class Model:
    text: str = ""
    late: bool = False


@dataclass(frozen=True)
class Late:
    """The message a command sends after the screen is gone."""


class Typing:
    """A screen that types printable keys into its model and quits with it on enter."""

    def __init__(
        self, *, layout: str = "full", extra: Callable[[Model, object], object] | None = None
    ) -> None:
        self.seen: list[object] = []
        self.threads: list[str] = []
        self._extra = extra
        self.screen: Screen[Model, str] = Screen(
            init=lambda: (Model(), []),
            update=self._update,
            view=lambda model, frame: f"typed: {model.text}",
            title="Typing",
            command="untaped typing",
            alternative="untaped typing --format json",
            layout=layout,  # type: ignore[arg-type]
        )

    def _update(self, model: Model, message: object) -> tuple[Model, Sequence[Cmd]]:
        self.seen.append(message)
        self.threads.append(threading.current_thread().name)
        if self._extra is not None:
            handled = self._extra(model, message)
            if handled is not None:
                return handled  # type: ignore[return-value]
        match message:
            case Key("enter"):
                return model, [Cmd.send(Quit(model.text))]
            case Key(name) if len(name) == 1:
                return replace(model, text=model.text + name), []
            case Paste(text):
                return replace(model, text=model.text + text), []
        return model, []


def _run(
    typing: Typing, keys: str, *, output: Output | None = None, close: bool = True
) -> Quit[str] | Cancel:
    with create_pipe_input() as pipe:
        pipe.send_text(keys)
        if close:
            pipe.close()  # a missing quit reads EOF instead of hanging
        return run_terminal_screen(
            typing.screen, input=pipe, output=output or DummyOutput(), theme=THEME
        )


class Recorder:
    """An output that records its escapes and has a size tests can change."""

    def __init__(self, size: tuple[int, int] = (80, 24), term: str = "xterm-256color") -> None:
        self.buffer = io.StringIO()
        self.size = Size(rows=size[1], columns=size[0])
        self.output = Vt100_Output(
            self.buffer, get_size=lambda: self.size, term=term, enable_cpr=False
        )

    def text(self) -> str:
        return self.buffer.getvalue()


@contextmanager
def _threaded(
    typing: Typing, output: Output | None = None
) -> Iterator[tuple[PipeInput, threading.Thread, list[object]]]:
    """Run ``typing`` on a worker thread so a test can steer it and look at its output."""
    outcome: list[object] = []
    with create_pipe_input() as pipe:

        def work() -> None:
            try:
                outcome.append(
                    run_terminal_screen(
                        typing.screen, input=pipe, output=output or DummyOutput(), theme=THEME
                    )
                )
            except BaseException as error:  # reported through the outcome list
                outcome.append(error)

        thread = threading.Thread(target=work, name="test-screen", daemon=True)
        thread.start()
        try:
            yield pipe, thread, outcome
        finally:
            pipe.close()
            thread.join(timeout=5)
        assert not thread.is_alive(), "the screen did not end"


def _wait_for(condition: Callable[[], bool], *, what: str, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(0.005)


def test_a_tiny_screen_runs_end_to_end() -> None:
    typing = Typing()
    outcome = _run(typing, "hi there" + ENTER)
    assert outcome == Quit("hi there")
    assert typing.seen[0] == Key("h")


def test_paste_arrives_as_one_message() -> None:
    typing = Typing()
    outcome = _run(typing, "\x1b[200~pasted text\x1b[201~" + ENTER)
    assert outcome == Quit("pasted text")
    assert [m for m in typing.seen if isinstance(m, Paste)] == [Paste("pasted text")]
    assert not [m for m in typing.seen if isinstance(m, Key) and m.name != "enter"]


def test_a_paste_keeps_printable_characters_only() -> None:
    typing = Typing()
    outcome = _run(typing, "\x1b[200~a\tb\nc\x1b[201~" + ENTER)
    assert outcome == Quit("abc")


def test_unbound_escape_sequences_do_not_type() -> None:
    typing = Typing()
    outcome = _run(typing, F1 + "api" + ENTER)
    assert outcome == Quit("api")


def test_named_keys_arrive_by_name() -> None:
    typing = Typing()
    keys = "\x1b[A\x1b[B\x1b[D\x1b[C\x1b[H\x1b[F\t\x1b[Z\x7f\x1b[3~\x15\x17\x12" + ENTER
    _run(typing, keys)
    names = [m.name for m in typing.seen if isinstance(m, Key)]
    assert names == [
        "up",
        "down",
        "left",
        "right",
        "home",
        "end",
        "tab",
        "shift-tab",
        "backspace",
        "delete",
        "ctrl-u",
        "ctrl-w",
        "ctrl-r",
        "enter",
    ]


def test_the_ctrl_c_key_is_a_message_the_screen_can_handle() -> None:
    def handle(model: Model, message: object) -> tuple[Model, list[Cmd]] | None:
        if message == Key("ctrl-c"):
            return replace(model, text=model.text + "!"), []
        return None

    typing = Typing(extra=handle)
    assert _run(typing, "a\x03\x13" + ENTER) == Quit("a!")
    assert [m.name for m in typing.seen if isinstance(m, Key)] == ["a", "ctrl-c", "ctrl-s", "enter"]


def test_the_escape_timeout_is_short() -> None:
    with create_pipe_input() as pipe:
        application, _host, _runtime = build_application(
            Typing().screen, input=pipe, output=DummyOutput(), theme=THEME
        )
    assert application.ttimeoutlen <= 0.1  # prompt_toolkit's default of 0.5 s feels like lag


def _reaction_time(typing: Typing, keys: str) -> tuple[float, list[object]]:
    """Seconds from sending ``keys`` (no closing) to the screen ending, and how it ended."""
    recorder = Recorder()
    with _threaded(typing, recorder.output) as (pipe, thread, outcome):
        _wait_for(lambda: "typed:" in recorder.text(), what="the first frame")
        started = time.monotonic()
        pipe.send_text(keys)
        thread.join(timeout=5)
        return time.monotonic() - started, outcome


def test_an_unhandled_escape_cancels_at_once() -> None:
    # The pipe stays open: only the escape timeout tells a lone escape from a sequence.
    elapsed, outcome = _reaction_time(Typing(), ESC)
    assert outcome == [Cancel()]
    assert elapsed < REACTION_LIMIT


def test_an_unhandled_ctrl_c_key_cancels_as_an_interrupt() -> None:
    assert _run(Typing(), "\x03") == Cancel(interrupted=True)


def test_a_named_key_is_not_held_back_waiting_for_a_longer_sequence() -> None:
    # No binding of the application extends a named key (prompt_toolkit's emacs chords
    # like ctrl-c ">" are inactive without a focused buffer), so none waits out the
    # one-second key timeout: a chord added later must not make esc or ctrl-c lag.
    elapsed, outcome = _reaction_time(Typing(), "\x03")
    assert outcome == [Cancel(interrupted=True)]
    assert elapsed < REACTION_LIMIT


def test_closed_input_raises_eof() -> None:
    with pytest.raises(EOFError):
        _run(Typing(), "abc")


@pytest.mark.skipif(sys.platform == "win32", reason="SIGINT handling is not available here")
def test_an_external_sigint_interrupts() -> None:
    if threading.current_thread() is not threading.main_thread():
        pytest.skip("prompt_toolkit handles SIGINT only on the main thread")

    def interrupt(model: Model, message: object) -> tuple[Model, list[Cmd]] | None:
        if message == Key("x"):
            return model, [
                Cmd(lambda: os.kill(os.getpid(), signal.SIGINT))
            ]  # what `kill -INT` does
        return None

    with pytest.raises(KeyboardInterrupt):
        _run(Typing(extra=interrupt), "x", close=False)


def test_the_sigint_binding_exits_with_keyboard_interrupt() -> None:
    typing = Typing()
    with create_pipe_input() as pipe:
        application, host, runtime = build_application(
            typing.screen, input=pipe, output=DummyOutput(), theme=THEME
        )

        def start() -> None:
            host.bind_loop()
            host.apply(runtime.start)
            application.key_processor.send_sigint()

        with pytest.raises(KeyboardInterrupt):
            application.run(pre_run=start)


def test_a_screen_that_raises_ends_the_run_with_its_error() -> None:
    def explode(_model: Model, message: object) -> None:
        if message == Key("x"):
            raise RuntimeError("update blew up")

    with pytest.raises(RuntimeError, match="update blew up"):
        _run(Typing(extra=explode), "x", close=False)


def test_a_view_that_raises_ends_the_run_with_its_error() -> None:
    def view(_model: Model, _frame: Frame) -> str:
        raise RuntimeError("view blew up")

    typing = Typing()
    screen = replace(typing.screen, view=view)
    with create_pipe_input() as pipe, pytest.raises(RuntimeError, match="view blew up"):
        run_terminal_screen(screen, input=pipe, output=DummyOutput(), theme=THEME)


def test_full_enters_and_leaves_the_alternate_screen_and_inline_does_not() -> None:
    full, inline = Recorder(), Recorder()
    _run(Typing(layout="full"), "x" + ENTER, output=full.output)
    _run(Typing(layout="inline"), "x" + ENTER, output=inline.output)
    assert ALT_SCREEN_ON in full.text()
    assert ALT_SCREEN_OFF in full.text()
    assert full.text().index(ALT_SCREEN_ON) < full.text().index(ALT_SCREEN_OFF)
    assert "?1049" not in inline.text()


def test_the_view_is_painted_at_the_terminal_size() -> None:
    recorder = Recorder(size=(40, 6))
    _run(Typing(), "ab" + ENTER, output=recorder.output)
    assert "typed:" in recorder.text()
    assert "esc" in recorder.text()  # the pinned footer is on the screen


def test_a_resize_reaches_the_screen() -> None:
    recorder = Recorder(size=(80, 24))

    def quit_on_resize(model: Model, message: object) -> tuple[Model, list[Cmd]] | None:
        if isinstance(message, Resize):
            return model, [Cmd.send(Quit(f"{message.width}x{message.height}"))]
        return None

    typing = Typing(extra=quit_on_resize)
    with _threaded(typing, recorder.output) as (pipe, thread, outcome):
        _wait_for(lambda: "typed:" in recorder.text(), what="the first frame")
        recorder.size = Size(rows=10, columns=50)
        pipe.send_text("x")  # any key repaints, and the repaint sees the new size
        thread.join(timeout=3)
        assert outcome == [Quit("50x10")]


# --- one colour depth -------------------------------------------------------------

DEPTHS = [
    ({"COLORTERM": "truecolor"}, ColorDepth.DEPTH_24_BIT, "38;2;"),
    ({"TERM": "xterm-256color"}, ColorDepth.DEPTH_8_BIT, "38;5;"),
    ({"TERM": "xterm"}, ColorDepth.DEPTH_4_BIT, ""),
    ({"NO_COLOR": "1", "COLORTERM": "truecolor"}, ColorDepth.DEPTH_1_BIT, ""),
]


def _painted(environ: dict[str, str], style: str) -> tuple[ColorDepth, str]:
    """The application's colour depth and the raw ANSI its control paints for ``style``."""
    from rich.text import Text

    screen: Screen[None, None] = Screen(
        init=lambda: (None, []),
        update=lambda model, message: (model, []),
        view=lambda model, frame: Text("sample", style=style),
        title="Colour",
        command="untaped colour",
        alternative="untaped colour --format json",
    )
    with create_pipe_input() as pipe:
        application, _host, runtime = build_application(
            screen, input=pipe, output=DummyOutput(), theme=THEME, environ=environ
        )
        runtime.start()
        control = application.layout.current_control
        text = control.text()  # type: ignore[attr-defined]
        assert isinstance(text, ANSI)
        return application.color_depth, text.value


@pytest.mark.parametrize("index", range(len(DEPTHS)))
def test_color_depth_reaches_rich_and_prompt_toolkit(index: int) -> None:
    environ, depth, rich_code = DEPTHS[index]
    # Rich caches a Style's codes at first use, so every case gets a style of its own.
    style = f"#1{index}2030"
    application_depth, whole = _painted(environ, style)
    ansi = whole.split("\n")[0]  # the sample row; the footer's styles were cached by other tests
    assert application_depth is depth
    if rich_code:
        assert rich_code in ansi
    elif depth is ColorDepth.DEPTH_4_BIT:
        assert "38;2;" not in ansi
        assert "38;5;" not in ansi
        assert "\x1b[3" in ansi or "\x1b[9" in ansi
    else:
        assert "38;" not in ansi


def test_no_color_keeps_bold() -> None:
    _depth, whole = _painted({"NO_COLOR": "1"}, "bold #4a5b6c")
    ansi = whole.split("\n")[0]
    assert "\x1b[1m" in ansi
    assert "38;" not in ansi
    recorder = Recorder()
    no_color = {"NO_COLOR": "1"}
    typing = Typing()
    with create_pipe_input() as pipe:
        pipe.send_text(ENTER)
        run_terminal_screen(
            typing.screen, input=pipe, output=recorder.output, theme=THEME, environ=no_color
        )
    assert ";1m" in recorder.text() or "[1m" in recorder.text()  # the footer keys are bold
    assert "38;" not in recorder.text()


def _control_text(screen: Screen[object, object], environ: dict[str, str]) -> str:
    """The raw ANSI the application's own control paints for ``screen`` (the adapter's path)."""
    with create_pipe_input() as pipe:
        application, _host, runtime = build_application(
            screen, input=pipe, output=DummyOutput(), theme=THEME, environ=environ
        )
        runtime.start()
        text = application.layout.current_control.text()  # type: ignore[attr-defined]
        assert isinstance(text, ANSI)
        return text.value


def test_a_view_string_is_literal_text_through_the_adapter() -> None:
    """Markup, emoji and highlight stay off on the adapter's own console, not only the driver's."""
    text = "[WIP] fix [/] :smile: 42 https://example.com"
    screen: Screen[None, None] = Screen(
        init=lambda: (None, []),
        update=lambda model, message: (model, []),
        view=lambda model, frame: text,
        title="Literal",
        command="untaped literal",
        alternative="untaped literal --format json",
    )
    painted = _control_text(screen, {"TERM": "xterm-256color"})  # type: ignore[arg-type]
    assert text in painted  # no escapes inside it: no highlight, no markup, no emoji


def test_the_first_frame_after_a_resize_is_painted_at_the_new_size() -> None:
    """Not one frame at the old size until the ``Resize`` message gets through."""
    recorder = Recorder(size=(80, 24))
    screen: Screen[None, None] = Screen(
        init=lambda: (None, []),
        update=lambda model, message: (model, []),  # ignores Resize: only the paint can know
        view=lambda model, frame: f"size {frame.width}x{frame.height}",
        title="Size",
        command="untaped size",
        alternative="untaped size --format json",
    )
    with create_pipe_input() as pipe:
        application, _host, runtime = build_application(
            screen, input=pipe, output=recorder.output, theme=THEME, environ={}
        )
        runtime.start()
        control = application.layout.current_control
        assert "size 80x23" in control.text().value  # type: ignore[attr-defined]
        recorder.size = Size(rows=10, columns=50)

        async def repaint() -> str:
            frame: str = control.text().value  # type: ignore[attr-defined]
            return frame

        assert "size 50x9" in asyncio.run(repaint())
        assert runtime.size == (50, 10)


def test_an_inline_screen_erases_itself_and_a_full_one_owns_the_screen() -> None:
    with create_pipe_input() as pipe:
        inline, _, _ = build_application(
            Typing(layout="inline").screen, input=pipe, output=DummyOutput(), theme=THEME
        )
        full, _, _ = build_application(
            Typing(layout="full").screen, input=pipe, output=DummyOutput(), theme=THEME
        )
    assert (inline.erase_when_done, inline.full_screen) == (True, False)
    assert (full.erase_when_done, full.full_screen) == (False, True)


# --- commands ---------------------------------------------------------------------


def test_a_write_command_survives_quit() -> None:
    release = threading.Event()
    started = threading.Event()
    finished = threading.Event()

    daemon: list[bool] = []

    def write() -> object:
        daemon.append(threading.current_thread().daemon)
        started.set()
        assert release.wait(timeout=5)
        finished.set()
        return None

    def on_key(model: Model, message: object) -> tuple[Model, list[Cmd]] | None:
        if message == Key("w"):
            return model, [Cmd(write, write=True, name="write")]
        return None

    recorder = Recorder()
    typing = Typing(extra=on_key)
    with _threaded(typing, recorder.output) as (pipe, thread, outcome):
        pipe.send_text("w")
        assert started.wait(timeout=5)
        pipe.send_text(ESC)  # back, unhandled: the screen quits while the write runs
        _wait_for(lambda: "saving…" in recorder.text(), what="the saving footer")
        assert thread.is_alive()
        assert not finished.is_set()
        release.set()
        thread.join(timeout=5)
        assert finished.is_set()
        assert outcome == [Cancel()]
    assert daemon == [False]  # the interpreter must not kill a write half-way


def test_a_suspend_command_runs_with_the_screen_left() -> None:
    recorder = Recorder()
    seen: dict[str, object] = {}

    def suspended() -> object:
        seen["screen_left"] = recorder.text().endswith(ALT_SCREEN_OFF) or (
            ALT_SCREEN_OFF in recorder.text()
        )
        seen["thread"] = threading.current_thread().name
        return Late()

    def on_message(model: Model, message: object) -> tuple[Model, list[Cmd]] | None:
        if message == Key("s"):
            return model, [Cmd(suspended, suspend=True, name="suspended")]
        if isinstance(message, Late):
            return replace(model, text="resumed"), []
        return None

    typing = Typing(extra=on_message)
    with _threaded(typing, recorder.output) as (pipe, thread, outcome):
        pipe.send_text("s")
        _wait_for(lambda: recorder.text().count(ALT_SCREEN_ON) >= 2, what="the screen to return")
        _wait_for(lambda: "resumed" in recorder.text(), what="the command's message on screen")
        pipe.send_text(ENTER)
        thread.join(timeout=5)
        assert outcome == [Quit("resumed")]
    assert seen["screen_left"] is True
    assert seen["thread"] != "test-screen"  # run_in_terminal(in_executor=True)


def test_a_late_background_result_after_quit_is_ignored() -> None:
    release = threading.Event()
    done = threading.Event()

    daemon: list[bool] = []

    def slow() -> object:
        daemon.append(threading.current_thread().daemon)
        assert release.wait(timeout=5)
        done.set()
        return Late()

    def on_key(model: Model, message: object) -> tuple[Model, list[Cmd]] | None:
        if message == Key("b"):
            return model, [Cmd(slow, name="slow")]
        return None

    typing = Typing(extra=on_key)
    outcome = _run(typing, "b" + ENTER, close=False)
    assert outcome == Quit("")  # quit did not wait for the command
    _wait_for(lambda: bool(daemon), what="the command to start")
    assert daemon == [True]  # a background command never keeps the interpreter alive
    release.set()
    assert done.wait(timeout=5)
    time.sleep(0.05)  # the result has nowhere to go: no error, no delivery
    assert Late() not in typing.seen


def test_updates_run_on_the_event_loop_thread() -> None:
    typing = Typing()
    _run(typing, "ab" + ENTER)
    assert len(set(typing.threads)) == 1


def test_a_host_that_is_not_running_ignores_calls() -> None:
    host = TerminalHost()
    ran: list[str] = []
    host.post(lambda: ran.append("post"))  # no loop yet: nowhere to run it
    host.spawn(lambda: ran.append("suspend"), kind="suspend")
    host.redraw()
    host.finish()
    assert ran == []


def test_the_host_finishes_the_application_once() -> None:
    typing = Typing()
    with create_pipe_input() as pipe:
        application, host, runtime = build_application(
            typing.screen, input=pipe, output=DummyOutput(), theme=THEME
        )

        def start() -> None:
            host.bind_loop()
            host.apply(runtime.start)
            runtime.outcome = Quit("done")
            host.finish()
            host.finish()  # a second finish must not try to exit again

        assert application.run(pre_run=start) == Quit("done")
