"""The adapter on a real pseudo-terminal, opened as the controlling terminal is."""

from __future__ import annotations

import os
import select
import signal
import sys
import threading
import time
from typing import TextIO

import pytest

termios = pytest.importorskip("termios")  # POSIX only

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or not hasattr(os, "openpty"), reason="needs a POSIX pseudo-terminal"
)

from prompt_toolkit.input.defaults import create_input  # noqa: E402
from prompt_toolkit.output.defaults import create_output  # noqa: E402

from untaped.screen.core import Cancel, Cmd, Frame, Key, Quit, Screen  # noqa: E402
from untaped.screen.terminal import run_terminal_screen  # noqa: E402
from untaped.theme import BUILTIN_THEMES  # noqa: E402


def _screen(layout: str) -> Screen[str, str]:
    def update(model: str, message: object) -> tuple[str, list[Cmd]]:
        if message == Key("enter"):
            return model, [Cmd.send(Quit(model))]
        if isinstance(message, Key) and len(message.name) == 1:
            return model + message.name, []
        return model, []

    def view(model: str, frame: Frame) -> str:
        return f"typed: {model}"

    return Screen(
        init=lambda: ("", []),
        update=update,
        view=view,
        title="Pty",
        command="untaped pty",
        alternative="untaped pty --format json",
        layout=layout,  # type: ignore[arg-type]
    )


def _drain(master: int, *, until: bytes, timeout: float = 5.0) -> bytes:
    """Read what the screen wrote to the terminal until ``until`` shows up."""
    seen = b""
    deadline = time.monotonic() + timeout
    while until not in seen and time.monotonic() < deadline:
        ready, _, _ = select.select([master], [], [], 0.05)
        if ready:
            try:
                seen += os.read(master, 65536)
            except OSError:  # the slave side closed
                break
    return seen


@pytest.mark.parametrize("layout", ["full", "inline"])
def test_a_screen_runs_on_a_pty_opened_as_two_handles(layout: str) -> None:
    master, slave = os.openpty()
    path = os.ttyname(slave)
    # A pty slave opens read-only and write-only as separate handles ("r+" fails on a tty).
    with open(path, encoding="utf-8") as tty_in, open(path, "w", encoding="utf-8") as tty_out:
        result: list[object] = []

        def work() -> None:
            try:
                result.append(
                    run_terminal_screen(
                        _screen(layout),
                        input=create_input(tty_in),
                        output=create_output(tty_out),
                        theme=BUILTIN_THEMES["default"],
                        environ={"TERM": "xterm-256color"},
                    )
                )
            except BaseException as error:
                result.append(error)

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        seen = _drain(master, until=b"typed:")
        os.write(master, b"hi\r")
        seen += _drain(master, until=b"\x1b[?1049l" if layout == "full" else b"\x1b[?2004l")
        thread.join(timeout=5)
        assert not thread.is_alive()
    os.close(master)
    os.close(slave)
    assert result == [Quit("hi")]
    assert (b"\x1b[?1049h" in seen) is (layout == "full")
    assert (b"\x1b[?1049l" in seen) is (layout == "full")


def test_escape_on_a_pty_cancels() -> None:
    master, slave = os.openpty()
    path = os.ttyname(slave)
    with open(path, encoding="utf-8") as tty_in, open(path, "w", encoding="utf-8") as tty_out:
        result: list[object] = []

        def work() -> None:
            try:
                result.append(
                    run_terminal_screen(
                        _screen("full"),
                        input=create_input(tty_in),
                        output=create_output(tty_out),
                        theme=BUILTIN_THEMES["default"],
                        environ={},
                    )
                )
            except BaseException as error:
                result.append(error)

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        _drain(master, until=b"typed:")
        os.write(master, b"\x1b")
        thread.join(timeout=5)
        assert not thread.is_alive()
    os.close(master)
    os.close(slave)
    assert result == [Cancel()]


def test_ui_run_draws_on_the_controlling_terminal_when_stdin_is_piped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real backend, end to end: piped stdin and redirected stderr, one pty for both."""
    import io

    from untaped.ui import UiContext

    master, slave = os.openpty()
    path = os.ttyname(slave)

    def controlling_terminal(*, write: bool = False) -> TextIO:
        return open(path, "w" if write else "r", encoding="utf-8")

    monkeypatch.setattr("untaped.ui.open_controlling_terminal", controlling_terminal)
    ui = UiContext(stdin=io.StringIO("piped data"), stderr=io.StringIO())
    result: list[object] = []

    def work() -> None:
        try:
            result.append(ui.run(_screen("inline")))
        except BaseException as error:
            result.append(error)

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    seen = _drain(master, until=b"typed:")
    os.write(master, b"ok\r")
    thread.join(timeout=5)
    assert not thread.is_alive()
    os.close(master)
    os.close(slave)
    assert result == ["ok"]
    assert b"typed:" in seen
    assert (ui.stdin.read(), ui.stderr.getvalue()) == (
        "piped data",
        "",
    )  # nothing went to the files


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGHUP], ids=["sigterm", "sighup"])
def test_a_termination_signal_restores_the_terminal_and_exits(
    sig: signal.Signals,
) -> None:
    """``kill`` and a closing terminal must not leave the alternate screen and raw mode behind."""
    if threading.current_thread() is not threading.main_thread():
        pytest.skip("signal handlers are installed on the main thread only")

    def guard(number: int, _frame: object) -> None:
        # What a missing adapter handler leaves: fail the test instead of killing the run.
        raise AssertionError(f"signal {number} reached the handler installed before the screen")

    before = signal.signal(sig, guard)
    screen: Screen[str, str] = Screen(
        init=lambda: ("", [Cmd(lambda: os.kill(os.getpid(), sig))]),  # `kill` from outside
        update=lambda model, message: (model, []),
        view=lambda model, frame: "waiting",
        title="Pty",
        command="untaped pty",
        alternative="untaped pty --format json",
    )
    master, slave = os.openpty()
    path = os.ttyname(slave)
    try:
        with open(path, encoding="utf-8") as tty_in, open(path, "w", encoding="utf-8") as tty_out:
            cooked = termios.tcgetattr(tty_in)[3] & (termios.ICANON | termios.ECHO)
            assert cooked == termios.ICANON | termios.ECHO
            with pytest.raises(SystemExit) as raised:
                run_terminal_screen(
                    screen,
                    input=create_input(tty_in),
                    output=create_output(tty_out),
                    theme=BUILTIN_THEMES["default"],
                    environ={"TERM": "xterm-256color"},
                )
            assert raised.value.code == 128 + sig
            assert termios.tcgetattr(tty_in)[3] & (termios.ICANON | termios.ECHO) == cooked
        seen = _drain(master, until=b"\x1b[?1049l")
        assert b"\x1b[?1049h" in seen
        assert b"\x1b[?1049l" in seen  # the alternate screen was left
        assert signal.getsignal(sig) is guard  # and the handler from before is back
    finally:
        signal.signal(sig, before)
        os.close(master)
        os.close(slave)


def test_an_answered_inline_prompt_leaves_its_record_line_on_the_terminal() -> None:
    from untaped.prompts import PromptToolkitPromptBackend

    master, slave = os.openpty()
    path = os.ttyname(slave)
    with open(path, encoding="utf-8") as tty_in, open(path, "w", encoding="utf-8") as tty_out:
        result: list[object] = []

        def work() -> None:
            backend = PromptToolkitPromptBackend(stdin=tty_in, stderr=tty_out)
            try:
                result.append(backend.text("Name", default=None))
            except BaseException as error:
                result.append(error)

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        _drain(master, until=b"Name")
        os.write(master, b"dev\r")
        seen = _drain(master, until=b"Name: dev\n")
        thread.join(timeout=5)
        assert not thread.is_alive()
    os.close(master)
    os.close(slave)
    assert result == ["dev"]
    assert seen.endswith(b"Name: dev\r\n")
