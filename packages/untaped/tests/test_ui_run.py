"""``UiContext.run``: results, cancellation, and the terminal rule every screen shares."""

from __future__ import annotations

import io
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

import pytest

from untaped.cli import create_app, report_errors
from untaped.errors import ConfigError, OperationCancelledError, PromptInterruptedError, UsageError
from untaped.prompts import (
    open_controlling_terminal,
    reset_terminal_override,
    set_terminal_override,
)
from untaped.screen.core import Cancel, Cmd, Key, Quit, Screen
from untaped.stability import Experimental, function_mark
from untaped.testing import CliInvoker, ScreenKeys, ScriptedPromptBackend, TtyStringIO
from untaped.theme import ThemeSpec
from untaped.ui import UiContext, no_terminal_message


def _screen(title: str = "Name it") -> Screen[str, str]:
    def update(model: str, message: object) -> tuple[str, Sequence[Cmd]]:
        if message == Key("enter"):
            return model, [Cmd.send(Quit(model))]
        if isinstance(message, Key) and len(message.name) == 1:
            return model + message.name, []
        return model, []

    return Screen(
        init=lambda: ("", []),
        update=update,
        view=lambda model, frame: f"name: {model}",
        title=title,
        command="untaped demo",
        alternative="untaped demo --name NAME",
    )


def _interactive(backend: ScriptedPromptBackend) -> UiContext:
    return UiContext(stdin=TtyStringIO(), stderr=TtyStringIO(), prompt_backend=backend)


def test_run_returns_the_quit_result() -> None:
    backend = ScriptedPromptBackend(screens=["alpha"])
    assert _interactive(backend).run(_screen()) == "alpha"
    assert backend.calls == [("run_screen", "Name it")]


def test_run_cancel_raises_operation_cancelled() -> None:
    backend = ScriptedPromptBackend(screens=[Cancel()])
    with pytest.raises(OperationCancelledError):
        _interactive(backend).run(_screen())


def test_run_interrupt_raises_prompt_interrupted() -> None:
    backend = ScriptedPromptBackend(screens=[Cancel(interrupted=True)])
    with pytest.raises(PromptInterruptedError, match="prompt cancelled"):
        _interactive(backend).run(_screen())


@pytest.mark.parametrize(
    ("raised", "expected"),
    [(KeyboardInterrupt(), PromptInterruptedError), (EOFError(), ConfigError)],
)
def test_run_maps_terminal_exceptions_like_every_prompt(
    raised: BaseException, expected: type[Exception]
) -> None:
    backend = ScriptedPromptBackend(screens=[raised])
    with pytest.raises(expected, match="prompt cancelled"):
        _interactive(backend).run(_screen())


def test_run_without_a_terminal_names_the_command_and_alternative() -> None:
    app = create_app(name="demo")

    @app.default
    def go() -> None:
        with report_errors():
            UiContext(stdin=io.StringIO()).run(_screen())

    result = CliInvoker().invoke(app, [])  # the harness has no controlling terminal
    assert result.exit_code == 2
    assert "`untaped demo` needs a terminal; use `untaped demo --name NAME`" in result.stderr


def test_the_no_terminal_message_is_built_in_one_place() -> None:
    assert no_terminal_message("untaped x", "untaped x plan") == (
        "`untaped x` needs a terminal; use `untaped x plan`"
    )


@dataclass
class Opened:
    """A fake controlling terminal: records every open and what the screen saw."""

    modes: list[bool]
    handles: list[TextIO]

    def __call__(self, *, write: bool = False) -> TextIO:
        handle = TtyStringIO()
        self.modes.append(write)
        self.handles.append(handle)
        return handle


class _Spy(ScriptedPromptBackend):
    """A scripted backend that remembers the streams the context pointed it at."""

    def __init__(self, ui_holder: list[UiContext], answer: object = "ok") -> None:
        super().__init__(screens=[answer])
        self._holder = ui_holder
        self.streams: list[tuple[TextIO, TextIO]] = []

    def run_screen[M, R](self, screen: Screen[M, R], *, theme: ThemeSpec) -> Quit[R] | Cancel:
        ui = self._holder[0]
        self.streams.append((ui.stdin, ui.stderr))
        return super().run_screen(screen, theme=theme)


def _spied(
    monkeypatch: pytest.MonkeyPatch, *, stdin: TextIO, stderr: TextIO, answer: object = "ok"
) -> tuple[UiContext, _Spy, Opened]:
    opened = Opened([], [])
    monkeypatch.setattr("untaped.ui.open_controlling_terminal", opened)
    holder: list[UiContext] = []
    spy = _Spy(holder, answer)
    ui = UiContext(stdin=stdin, stderr=stderr, prompt_backend=spy)
    holder.append(ui)
    return ui, spy, opened


def test_run_uses_the_streams_when_both_are_terminals(monkeypatch: pytest.MonkeyPatch) -> None:
    stdin, stderr = TtyStringIO(), TtyStringIO()
    ui, spy, opened = _spied(monkeypatch, stdin=stdin, stderr=stderr)
    assert ui.run(_screen()) == "ok"
    assert spy.streams == [(stdin, stderr)]
    assert opened.modes == []


def test_run_uses_the_controlling_terminal_when_stdin_is_piped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ui, spy, opened = _spied(monkeypatch, stdin=io.StringIO("piped"), stderr=TtyStringIO())
    ui.run(_screen())
    assert opened.modes == [False, True]
    assert spy.streams[0][0] is opened.handles[0]


def test_run_uses_the_controlling_terminal_for_output_when_stderr_is_redirected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ui, spy, opened = _spied(monkeypatch, stdin=TtyStringIO(), stderr=io.StringIO())
    ui.run(_screen())
    assert opened.modes == [False, True]  # input, then output (write=True)
    assert spy.streams[0][1] is opened.handles[1]


def test_run_restores_the_streams_after_the_block(monkeypatch: pytest.MonkeyPatch) -> None:
    stdin, stderr = io.StringIO("piped"), io.StringIO()
    ui, _spy, opened = _spied(monkeypatch, stdin=stdin, stderr=stderr)
    ui.run(_screen())
    assert (ui.stdin, ui.stderr) == (stdin, stderr)
    assert all(handle.closed for handle in opened.handles)


def test_run_restores_the_streams_when_the_screen_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    stdin, stderr = io.StringIO("piped"), io.StringIO()
    ui, _spy, opened = _spied(monkeypatch, stdin=stdin, stderr=stderr, answer=Cancel())
    with pytest.raises(OperationCancelledError):
        ui.run(_screen())
    assert (ui.stdin, ui.stderr) == (stdin, stderr)
    assert all(handle.closed for handle in opened.handles)


def test_a_terminal_that_opens_for_input_but_not_output_is_closed_and_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handles: list[TextIO] = []

    def opener(*, write: bool = False) -> TextIO:
        if write:
            raise OSError("no write")
        handles.append(TtyStringIO())
        return handles[-1]

    monkeypatch.setattr("untaped.ui.open_controlling_terminal", opener)
    ui = UiContext(stdin=io.StringIO(), prompt_backend=ScriptedPromptBackend(screens=["x"]))
    with pytest.raises(UsageError, match="needs a terminal"):
        ui.run(_screen())
    assert handles[0].closed


def test_the_controlling_terminal_opens_read_only_or_write_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    device = tmp_path / "tty"
    device.write_text("typed")
    monkeypatch.setattr("untaped.prompts._CONTROLLING_TERMINAL", str(device))
    token = set_terminal_override(None)  # the harness installs a no-terminal opener
    try:
        with open_controlling_terminal() as reader:
            assert reader.read() == "typed"
            assert not reader.writable()
        with open_controlling_terminal(write=True) as writer:
            writer.write("drawn")
    finally:
        reset_terminal_override(token)
    assert device.read_text() == "drawn"


def test_the_terminal_override_serves_both_modes() -> None:
    token = set_terminal_override(lambda: io.StringIO("seen"))
    try:
        assert open_controlling_terminal().read() == "seen"
        assert open_controlling_terminal(write=True).read() == "seen"
    finally:
        reset_terminal_override(token)


# --- scripted screens -------------------------------------------------------------


def test_scripted_screens_result_cancel_exception_and_keys() -> None:
    backend = ScriptedPromptBackend(
        screens=["plain result", Cancel(), RuntimeError("boom"), ScreenKeys("a", "b", "enter")]
    )
    ui = _interactive(backend)
    screen = _screen()

    assert ui.run(screen) == "plain result"
    with pytest.raises(OperationCancelledError):
        ui.run(screen)
    with pytest.raises(RuntimeError, match="boom"):
        ui.run(screen)
    assert ui.run(screen) == "ab"  # the keys ran through the real screen
    assert backend.ran == [screen] * 4
    assert backend.calls == [("run_screen", "Name it")] * 4


def test_scripted_keys_that_do_not_end_the_screen_fail_loudly() -> None:
    backend = ScriptedPromptBackend(screens=[ScreenKeys("a")])
    with pytest.raises(ConfigError, match="did not end screen"):
        _interactive(backend).run(_screen())


def test_an_empty_screens_queue_names_the_screen() -> None:
    with pytest.raises(ConfigError, match="no scripted run_screen answer for prompt 'Name it'"):
        _interactive(ScriptedPromptBackend()).run(_screen())


def test_scripted_interrupt_applies_to_screens() -> None:
    backend = ScriptedPromptBackend(screens=["unused"], interrupt=True)
    with pytest.raises(PromptInterruptedError):
        _interactive(backend).run(_screen())


def test_ui_run_is_marked_experimental() -> None:
    assert isinstance(function_mark(UiContext.run), Experimental)
