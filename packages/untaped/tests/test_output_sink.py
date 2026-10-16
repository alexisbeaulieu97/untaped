"""The one stderr writer: no interleaving, no deadlocks, the scope slot, muting and holds."""

from __future__ import annotations

import contextvars
import io
import json
import logging
import threading
from collections.abc import Callable, Sequence
from contextlib import redirect_stderr
from typing import Any

import pytest
from rich.text import Text

from untaped import verbose
from untaped.cli import echo
from untaped.errors import ConfigError
from untaped.management import fix
from untaped.output.diagnostic import Debug, Diagnostic, Failure, Message
from untaped.output.policy import current_holder, enable_quiet, reset_quiet
from untaped.output.sink import (
    Diagnostics,
    current_sink,
    diagnostics_scope,
    exit_on_counted_failure,
    failure_exit_code,
    recording,
)
from untaped.screen.core import Cmd, Quit, Screen
from untaped.testing import ScriptedPromptBackend, TtyStringIO
from untaped.theme import ThemeSpec
from untaped.ui import UiContext

_DEADLINE = 10.0


def _bare_thread(target: Callable[[], None]) -> threading.Thread:
    """A thread that starts from an empty context, as a library's own thread would."""
    empty = contextvars.Context()
    return threading.Thread(target=lambda: empty.run(target), daemon=True)


def _finishes(target: Callable[[], None]) -> None:
    """Run ``target`` on a thread and fail (instead of hanging) if it never returns."""
    errors: list[BaseException] = []

    def run() -> None:
        try:
            target()
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(_DEADLINE)
    assert not worker.is_alive(), "deadlocked"
    if errors:
        raise errors[0]


# ---- (1) no interleaving ----------------------------------------------------


def test_json_lines_from_the_main_and_a_bare_thread_never_interleave(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNTAPED_DIAGNOSTICS", "json")
    stream = io.StringIO()

    def failures() -> None:
        for n in range(1_000):
            current_sink().emit(Failure.from_exception(ConfigError(f"bad {n}", category="auth")))

    codes: list[int] = []

    def run() -> None:
        with redirect_stderr(stream), diagnostics_scope():
            other = _bare_thread(failures)
            other.start()
            for n in range(10_000):
                Diagnostics().warning(f"warning {n}")
            other.join(_DEADLINE)
            codes.append(failure_exit_code())

    _finishes(run)
    [code] = codes
    lines = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert len(lines) == 11_000
    assert sum(line["kind"] == "untaped.failure" for line in lines) == 1_000
    assert code == 4


def test_text_lines_stay_whole_around_the_spinner() -> None:
    stream = TtyStringIO()

    def failures() -> None:
        for n in range(500):
            Diagnostics().failure(ConfigError(f"bad {n}"))

    codes: list[int] = []

    def run() -> None:
        with redirect_stderr(stream), diagnostics_scope():
            with Diagnostics().progress("Working"):
                other = _bare_thread(failures)
                other.start()
                for n in range(2_000):
                    Diagnostics().warning(f"careful {n}")
                other.join(_DEADLINE)
            codes.append(failure_exit_code())

    _finishes(run)
    [code] = codes

    *lines, _last = stream.getvalue().split("\n")
    shown = [line.rsplit("\r", 1)[-1] for line in lines]
    assert sorted(shown) == sorted(
        [f"warning: careful {n}" for n in range(2_000)] + [f"error: bad {n}" for n in range(500)]
    )
    # The spinner is redrawn after each line, so it is cleared before the next one.
    assert sum("Working" in line for line in lines[1:]) > len(lines) // 2
    assert code == 4


# ---- (2) and (3) no deadlocks -----------------------------------------------


def test_a_renderer_that_emits_while_lines_are_held_does_not_deadlock() -> None:
    stream = io.StringIO()

    def rendered(colour: bool) -> str:
        del colour
        current_sink().emit(Message(level="warning", message="from the renderer"))
        return "outer"

    def run() -> None:
        with redirect_stderr(stream), diagnostics_scope():
            with current_sink().hold():
                current_sink().emit(Message(level="info", message="o"), rendered=rendered)
            current_sink().close()

    _finishes(run)
    assert stream.getvalue() == "warning: from the renderer\nouter\n"


def test_a_renderer_that_logs_and_two_writing_threads_do_not_deadlock() -> None:
    stream = TtyStringIO()

    def rendered(colour: bool) -> str:
        del colour
        logging.getLogger("untaped.test").debug("rendering")
        return "line"

    def writer() -> None:
        for _ in range(200):
            current_sink().emit(Message(level="info", message="x"), rendered=rendered)

    def run() -> None:
        token = verbose.enable()
        try:
            with redirect_stderr(stream), diagnostics_scope(), Diagnostics().progress("Busy"):
                other = threading.Thread(target=contextvars.copy_context().run, args=(writer,))
                other.start()
                writer()
                other.join(_DEADLINE)
        finally:
            verbose.reset(token)

    _finishes(run)
    shown = [line.rsplit("\r", 1)[-1] for line in stream.getvalue().split("\n")[:-1]]
    assert shown.count("line") == 400
    assert shown.count("DEBUG untaped.test: rendering") == 400


# ---- (4) to (6) the scope slot ----------------------------------------------


def test_nested_and_sequential_scopes_leave_the_slot_empty() -> None:
    with diagnostics_scope():
        with diagnostics_scope():
            assert current_holder() is not None
        with diagnostics_scope():
            pass
    with diagnostics_scope():
        pass

    assert current_holder() is None


def test_scopes_overlapping_on_two_threads_keep_their_own_sinks() -> None:
    first_in, second_in, first_out = threading.Event(), threading.Event(), threading.Event()
    seen: dict[str, object] = {}

    def first() -> None:
        with diagnostics_scope():
            seen["first"] = current_sink()
            first_in.set()
            second_in.wait(_DEADLINE)
        first_out.set()

    def second() -> None:
        first_in.wait(_DEADLINE)
        with diagnostics_scope():
            mine = current_sink()
            second_in.set()
            first_out.wait(_DEADLINE)
            seen["second"] = mine
            seen["second after first left"] = current_sink()

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for each in threads:
        each.start()
    for each in threads:
        each.join(_DEADLINE)

    assert seen["second"] is seen["second after first left"]
    assert seen["second"] is not seen["first"]
    assert current_holder() is None


def test_a_bare_thread_that_outlives_its_scope_reaches_the_next_one() -> None:
    """The documented limit: a bare thread finds whichever scope is current."""
    go = threading.Event()

    def late() -> None:
        go.wait(_DEADLINE)
        current_sink().emit(Failure.from_exception(ConfigError("late", category="auth")))

    with diagnostics_scope():
        straggler = _bare_thread(late)
        straggler.start()
    with diagnostics_scope():
        go.set()
        straggler.join(_DEADLINE)
        assert failure_exit_code() == 4


# ---- (7) and (8) close and the default sink ---------------------------------


def test_close_twice_is_a_no_op_and_a_later_line_writes_plainly() -> None:
    stream = io.StringIO()
    with redirect_stderr(stream), diagnostics_scope():
        sink = current_sink()
        with sink.hold():
            sink.emit(Message(level="warning", message="held"))
            sink.close()
            sink.close()
            assert stream.getvalue() == "warning: held\n"
            straggler = _bare_thread(lambda: sink.emit(Message(level="warning", message="late")))
            straggler.start()
            straggler.join(_DEADLINE)

    assert stream.getvalue() == "warning: held\nwarning: late\n"


def test_the_default_sink_writes_to_the_stderr_of_the_moment(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert current_holder() is None
    current_sink().emit(Message(level="warning", message="unscoped"))

    assert capsys.readouterr().err == "warning: unscoped\n"


# ---- (9) and (10) what a line is, and what counts ---------------------------


def test_a_kindless_diagnostic_is_refused() -> None:
    class _Loose(Diagnostic):
        level = "warning"

    with pytest.raises(TypeError, match="declares no kind"):
        current_sink().emit(_Loose(message="x"))


class _QuotaFailure(Failure, kind="acme-tools.quota_failure"):
    quota: int


def test_a_failure_subclass_counts_and_emit_counts_like_failure() -> None:
    with redirect_stderr(io.StringIO()):
        with diagnostics_scope():
            current_sink().emit(
                _QuotaFailure(message="over", category="unavailable", system="acme", quota=3)
            )
            assert failure_exit_code() == 5
        with diagnostics_scope():
            Diagnostics().failure(ConfigError("bad", category="auth"))
            emitted = failure_exit_code()
        with diagnostics_scope():
            current_sink().emit(Failure.from_exception(ConfigError("bad", category="auth")))
            assert failure_exit_code() == emitted == 4


def test_a_kindless_failure_subclass_is_printed_as_a_plain_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Payload(Failure):
        extra: int = 0

    monkeypatch.setenv("UNTAPED_DIAGNOSTICS", "json")
    stream = io.StringIO()
    with redirect_stderr(stream), diagnostics_scope():
        Diagnostics().failure(_Payload(message="m", category="failed", system="x", extra=1))

    assert json.loads(stream.getvalue())["kind"] == "untaped.failure"
    assert "extra" not in json.loads(stream.getvalue())


# ---- (11) and (12) quiet and verbose ----------------------------------------


def test_quiet_mutes_info_success_and_progress_only() -> None:
    stream = io.StringIO()
    token = enable_quiet()
    try:
        with redirect_stderr(stream), diagnostics_scope():
            diag = Diagnostics()
            diag.info("info")
            diag.success("success")
            with diag.progress("progress"):
                pass
            diag.warning("warning")
            diag.hint("hint")
            diag.failure(ConfigError("failure"))
    finally:
        reset_quiet(token)

    assert stream.getvalue() == "warning: warning\nhint: hint\nerror: failure\n"


@pytest.mark.parametrize("structured", [False, True])
def test_quiet_never_mutes_an_unmuted_info(
    structured: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    if structured:
        monkeypatch.setenv("UNTAPED_DIAGNOSTICS", "json")
    stream = io.StringIO()
    token = enable_quiet()
    try:
        with redirect_stderr(stream), diagnostics_scope():
            diag = Diagnostics()
            diag.info("a preview", unmuted=True)
            diag.info(Text("a styled preview", style="bold"), unmuted=True)
            diag.info("commentary")
            diag.success("done")
    finally:
        reset_quiet(token)

    if structured:
        lines = [json.loads(line) for line in stream.getvalue().splitlines()]
        assert [line["message"] for line in lines] == ["a preview", "a styled preview"]
    else:
        assert stream.getvalue() == "a preview\na styled preview\n"


@pytest.mark.parametrize("quiet", [False, True])
def test_a_blank_info_is_a_text_separator(quiet: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    text, structured = io.StringIO(), io.StringIO()
    token = enable_quiet() if quiet else None
    try:
        with redirect_stderr(text), diagnostics_scope():
            Diagnostics().info("")
        monkeypatch.setenv("UNTAPED_DIAGNOSTICS", "json")
        with redirect_stderr(structured), diagnostics_scope():
            Diagnostics().info("")
    finally:
        reset_quiet(token)

    assert text.getvalue() == ("" if quiet else "\n")
    assert structured.getvalue() == ""


def test_a_message_masks_url_passwords_on_the_wire_only(monkeypatch: pytest.MonkeyPatch) -> None:
    text, structured = io.StringIO(), io.StringIO()
    line = "  from https://u:secret@host/pack.git"
    with redirect_stderr(text), diagnostics_scope():
        Diagnostics().info(line)
    monkeypatch.setenv("UNTAPED_DIAGNOSTICS", "json")
    with redirect_stderr(structured), diagnostics_scope():
        Diagnostics().info(line)
        Diagnostics().warning(line)

    assert text.getvalue() == line + "\n"
    messages = [json.loads(each)["message"] for each in structured.getvalue().splitlines()]
    # Unlike ``echo``, the text is kept as the line shows it: leading spaces too.
    assert messages == ["  from https://u:***@host/pack.git"] * 2


@pytest.mark.parametrize("structured", [False, True])
def test_debug_lines_appear_only_under_verbose(
    monkeypatch: pytest.MonkeyPatch, structured: bool
) -> None:
    if structured:
        monkeypatch.setenv("UNTAPED_DIAGNOSTICS", "json")
    quiet, loud = io.StringIO(), io.StringIO()
    debug = Debug(logger="untaped.http", message="GET /")
    with redirect_stderr(quiet), diagnostics_scope():
        current_sink().emit(debug)
    token = verbose.enable()
    try:
        with redirect_stderr(loud), diagnostics_scope():
            current_sink().emit(debug)
    finally:
        verbose.reset(token)

    assert quiet.getvalue() == ""
    if structured:
        assert json.loads(loud.getvalue()) == {
            "level": "debug",
            "kind": "untaped.debug",
            "logger": "untaped.http",
            "message": "GET /",
        }
    else:
        assert loud.getvalue() == "DEBUG untaped.http: GET /\n"


# ---- (13) and (14) screens and the relay ------------------------------------


class _EmittingBackend(ScriptedPromptBackend):
    """Emits a warning while its screen runs, then quits with ``done``."""

    def run_screen[M, R](self, screen: Screen[M, R], *, theme: ThemeSpec) -> Any:
        del theme
        Diagnostics().warning("while the screen ran")
        self.mid_screen = self.stream.getvalue()
        return Quit("done")

    stream: io.StringIO
    mid_screen: str = ""


def _screen() -> Screen[str, str]:
    def update(model: str, message: object) -> tuple[str, Sequence[Cmd]]:
        del message
        return model, []

    return Screen(
        init=lambda: ("", []),
        update=update,
        view=lambda model, frame: "x",
        title="t",
        command="untaped demo",
        alternative="untaped demo --x",
    )


@pytest.mark.parametrize("structured", [False, True])
def test_lines_emitted_while_a_screen_runs_print_after_it(
    monkeypatch: pytest.MonkeyPatch, structured: bool
) -> None:
    if structured:
        monkeypatch.setenv("UNTAPED_DIAGNOSTICS", "json")
    stream = TtyStringIO()
    backend = _EmittingBackend()
    backend.stream = stream
    ui = UiContext(stdin=TtyStringIO(), stderr=TtyStringIO(), prompt_backend=backend)
    with redirect_stderr(stream), diagnostics_scope():
        assert ui.run(_screen()) == "done"

    if structured:
        assert backend.mid_screen != ""  # the JSON sink is never held
    else:
        assert backend.mid_screen == ""
        assert stream.getvalue() == "warning: while the screen ran\n"


def test_the_fix_relay_writes_through_the_sink_around_the_spinner() -> None:
    stream = TtyStringIO()
    child = io.StringIO("child line one\nchild line two\n")
    into: list[str] = []
    with redirect_stderr(stream), diagnostics_scope(), Diagnostics().progress("Fixing"):
        fix._drain(child, into, relay=True)

    lines = stream.getvalue().split("\n")
    assert [line.rsplit("\r", 1)[-1] for line in lines[:2]] == ["child line one", "child line two"]
    assert "Fixing" in lines[1]  # redrawn after the first relayed line
    assert into == ["child line one\n", "child line two\n"]


def test_an_echoed_error_line_counts() -> None:
    with (
        pytest.warns(DeprecationWarning, match=r"ctx\.diag|Diagnostics"),
        redirect_stderr(io.StringIO()),
        diagnostics_scope(),
    ):
        echo("warning: y", err=True)
        exit_on_counted_failure()  # a warning counts nothing
        echo("error: x", err=True)
        with pytest.raises(SystemExit) as exited:
            exit_on_counted_failure()

    assert exited.value.code == 1


def test_recorded_diagnostics_are_the_lines_shown() -> None:
    token = enable_quiet()
    try:
        with recording() as emitted, redirect_stderr(io.StringIO()), diagnostics_scope():
            Diagnostics().info("muted")
            Diagnostics().warning("kept")
            current_sink().emit(Debug(logger="untaped.x", message="hidden"))
    finally:
        reset_quiet(token)

    assert [each.message for each in emitted] == ["kept"]
