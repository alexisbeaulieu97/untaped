"""Progress through the sink: the task stack, its one ticker, the line cadence and the hold.

Every timing test runs on ``progress_clock()``: ``advance()`` moves the clock
and runs a tick, so nothing waits on the background ticker. The spinner tests
at the end are the exception: they watch the ticker animate in real time.
"""

from __future__ import annotations

import io
import json
import threading
import time
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager, redirect_stderr
from contextvars import Context

import pytest
from cyclopts import App

from untaped.runtime import sink
from untaped.runtime.run import entered, start_run
from untaped.runtime.sink import (
    Diagnostics,
    _compose,
    current_sink,
    open_tasks,
)
from untaped.sdk._concurrency import bounded_map
from untaped.sdk.diagnostic import Failure, Progress, Stale
from untaped.sdk.errors import UntapedError
from untaped.sdk.progress import NO_PROGRESS, ProgressHandle
from untaped.sdk.testing import ProgressClock, TtyStringIO, invoke_cli, progress_clock

_BRAILLE = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
#: Bounds a join in these tests: a hung ticker fails the test instead of the run.
_JOIN = 5.0


@contextmanager
def _session(
    stream: io.StringIO, monkeypatch: pytest.MonkeyPatch | None = None, *, json_lines: bool = False
) -> Iterator[ProgressClock]:
    """A scope writing to ``stream``, on a test clock."""
    if json_lines:
        assert monkeypatch is not None
        monkeypatch.setenv("UNTAPED_DIAGNOSTICS", "json")
    with progress_clock() as clock, redirect_stderr(stream), entered(start_run()):
        yield clock


def _progress(
    label: str, *, verbose: bool = False, quiet: bool = False
) -> AbstractContextManager[ProgressHandle]:
    return current_sink().progress(label, verbose=verbose, quiet=quiet)


def _lines(stream: io.StringIO) -> list[str]:
    return stream.getvalue().splitlines()


def _ticker_alive() -> bool:
    return any(thread.name == "untaped-progress" for thread in threading.enumerate())


def _wait_for_no_ticker() -> None:
    deadline = time.monotonic() + _JOIN
    while _ticker_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not _ticker_alive()


# --------------------------------------------------------------------------- #
# The cadence
# --------------------------------------------------------------------------- #


def test_the_line_cadence_on_a_pipe() -> None:
    stream = io.StringIO()
    with _session(stream) as clock, _progress("Syncing") as p:
        p.update(phase="listing", done=0, total=100)
        clock.advance(1.9)
        assert stream.getvalue() == ""  # the quiet period
        clock.advance(0.1)  # the late start line, from the tick, with the current state
        assert _lines(stream) == ["Syncing · listing 0/100 (2s)"]
        p.update(done=5)  # same 10 % step: nothing
        p.update(done=10)  # a step
        p.update(done=15)
        p.update("note")  # a message alone waits for the heartbeat
        assert _lines(stream)[1:] == ["Syncing · listing 10/100 (2s)"]
        clock.advance(29.9)
        assert len(_lines(stream)) == 2
        clock.advance(0.1)  # the heartbeat: same counts, a larger elapsed
        p.update(phase="fetching", done=0, total=2)  # a phase change
        p.update("last", done=1)
    assert _lines(stream)[2:] == [
        "Syncing · listing 15/100 · note (32s)",
        "Syncing · fetching 0/2 (32s)",
        "Syncing · fetching 1/2 · last (32s)",
        "Syncing: done · last (32s)",
    ]


def test_a_task_that_ends_inside_the_quiet_period_prints_nothing() -> None:
    stream = io.StringIO()
    with _session(stream) as clock, _progress("Loading") as p:
        p.update(phase="one", done=1, total=1)
        clock.advance(1.5)
    assert stream.getvalue() == ""


def test_the_line_cadence_under_json(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = io.StringIO()
    with _session(stream, monkeypatch, json_lines=True) as clock, _progress("Syncing") as p:
        p.update("4 changed", phase="probing refs", done=150, total=780)
        clock.advance(2.1)
    start, end = (json.loads(line) for line in _lines(stream))
    assert start == {
        "level": "progress",
        "kind": "untaped.progress",
        "message": "Syncing · probing refs 150/780 · 4 changed (2s)",
        "label": "Syncing",
        "phase": "probing refs",
        "done": 150,
        "total": 780,
        "elapsed": 2.1,
        "state": "running",
    }
    assert (end["message"], end["state"], end["phase"], end["done"]) == (
        "Syncing: done · 4 changed (2s)",
        "done",
        None,
        None,
    )


@pytest.mark.parametrize("error", [ValueError("boom"), KeyboardInterrupt()])
def test_a_task_that_raises_ends_failed(error: BaseException) -> None:
    stream = io.StringIO()
    with pytest.raises(type(error)), _session(stream) as clock, _progress("Syncing"):
        clock.advance(3)
        raise error
    assert _lines(stream) == ["Syncing (3s)", "Syncing: failed (3s)"]


def test_a_failed_task_without_a_start_line_prints_nothing() -> None:
    stream = io.StringIO()
    with pytest.raises(ValueError), _session(stream), _progress("Syncing"):
        raise ValueError("boom")
    assert stream.getvalue() == ""


def test_verbose_prints_every_update_from_the_first_and_keeps_the_heartbeat() -> None:
    stream = io.StringIO()
    with _session(stream) as clock, _progress("Syncing", verbose=True) as p:
        p.update("a", done=1, total=3)
        p.update("b", done=1, total=3)
        clock.advance(29.9)  # a tick is no update: nothing until the heartbeat
        clock.advance(0.1)
    assert _lines(stream) == [
        "Syncing (0s)",
        "Syncing 1/3 · a (0s)",
        "Syncing 1/3 · b (0s)",
        "Syncing 1/3 · b (30s)",
        "Syncing: done · b (30s)",
    ]


def test_verbose_json_prints_a_line_per_item(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = io.StringIO()
    with _session(stream, monkeypatch, json_lines=True), _progress("Many", verbose=True) as p:
        for index in range(1, 781):
            p.update(f"item {index}", done=index, total=780)
    states = [json.loads(line)["state"] for line in _lines(stream)]
    # the open, one per item, the end: --verbose -f json floods, by design
    assert (len(states), states[-1]) == (782, "done")


def test_quiet_mutes_progress_and_nothing_else(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_QUIET", "1")
    stream = io.StringIO()
    with _session(stream) as clock, _progress("Syncing", quiet=True) as p:
        p.update(phase="x", done=1, total=2)
        clock.advance(40)
        Diagnostics().warning("slow host")
        Diagnostics().failure(UntapedError("it broke"), item="acme/x")
    assert _lines(stream) == ["warning: slow host", "error: acme/x: it broke"]


def test_a_stale_line_inside_the_quiet_period_prints_alone() -> None:
    """The quiet period's documented cost: on a pipe no progress line comes before it."""
    stream = io.StringIO()
    stale = Stale(
        message="source prod: cached data",
        plugin="ansible",
        cache="ansible.index",
        key="prod",
        refreshed_at="2026-10-09T10:00:00Z",
        age_seconds=1.0,
        max_age_seconds=1,
    )
    with _session(stream) as clock, _progress("Refreshing source prod"):
        Diagnostics().emit(stale)
        clock.advance(1)
    assert _lines(stream) == ["warning: source prod: cached data"]


# --------------------------------------------------------------------------- #
# The line's shape and the update rules
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("parts", "line"),
    [
        ({}, "L (9s)"),
        ({"phase": "p"}, "L · p (9s)"),
        ({"done": 3, "total": 12}, "L 3/12 (9s)"),
        ({"done": 3}, "L 3 (9s)"),
        ({"text": "t"}, "L · t (9s)"),
        ({"phase": "p", "done": 3, "total": 12, "text": "t"}, "L · p 3/12 · t (9s)"),
        ({"state": "done", "phase": "p", "done": 3, "total": 12}, "L: done (9s)"),
        ({"state": "done", "text": "t"}, "L: done · t (9s)"),
        ({"state": "failed"}, "L: failed (9s)"),
    ],
)
def test_the_line_is_composed_one_way(parts: dict[str, object], line: str) -> None:
    kwargs: dict[str, object] = {"phase": None, "done": None, "total": None, "text": None}
    assert _compose("L", **(kwargs | parts), elapsed=9.4) == line  # type: ignore[arg-type]


def test_the_seconds_round_half_up() -> None:
    nothing: dict[str, None] = {"phase": None, "done": None, "total": None, "text": None}
    assert _compose("L", **nothing, elapsed=46.8) == "L (47s)"
    assert _compose("L", **nothing, elapsed=0.4) == "L (0s)"


def test_none_keeps_a_field_a_new_phase_resets_the_counts_and_the_message_is_per_update() -> None:
    stream = io.StringIO()
    with _session(stream), _progress("T", verbose=True) as p:
        p.update("first", phase="a", done=1, total=4)
        p.update(done=2)  # phase and total kept; the message cleared
        p.update(phase="b")  # a new phase: counts reset
        p.update(phase="b", total=9)  # the same phase: counts kept
    assert _lines(stream)[1:-1] == [
        "T · a 1/4 · first (0s)",
        "T · a 2/4 (0s)",
        "T · b (0s)",
        "T · b (0s)",  # a total without done prints no counts
    ]


# --------------------------------------------------------------------------- #
# Nesting and threads
# --------------------------------------------------------------------------- #


def test_a_task_inside_a_task_is_a_phase_of_the_outer_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = io.StringIO()
    with _session(stream, monkeypatch, json_lines=True), _progress("Outer", verbose=True) as outer:
        outer.update(phase="setup")
        with _progress("Inner", verbose=True) as inner:
            inner.update("x", phase="delete", done=2, total=3)
        outer.update(done=1)
    lines = [json.loads(line) for line in _lines(stream)]
    nested = lines[3]
    assert (nested["label"], nested["phase"], nested["message"]) == (
        "Outer",
        "Inner · delete",
        "Outer · Inner · delete 2/3 · x (0s)",
    )
    assert [line["phase"] for line in lines[-2:]] == ["setup", None]  # reverted, then done
    assert sum(line["state"] == "done" for line in lines) == 1  # the inner task has no end line


def test_a_task_opened_on_a_worker_or_a_bare_thread_attaches() -> None:
    stream = io.StringIO()
    seen: list[str] = []

    def work(name: str) -> None:
        with _progress(name, verbose=True) as handle:
            handle.update(done=1, total=1)

    with _session(stream), _progress("Outer", verbose=True):
        bounded_map(work, ["worker"], concurrency=1, on_each=lambda _item, _result: None)
        thread = threading.Thread(target=Context().run, args=(work, "bare"), name="bare")
        thread.start()
        thread.join(_JOIN)
        assert not thread.is_alive()
        seen = _lines(stream)
    assert "Outer · worker 1/1 (0s)" in seen
    assert "Outer · bare 1/1 (0s)" in seen


def test_two_inner_tasks_closing_out_of_order_leave_the_right_phase() -> None:
    stream = io.StringIO()
    with _session(stream), _progress("Outer", verbose=True) as outer:
        first = _progress("one", verbose=True)
        second = _progress("two", verbose=True)
        first.__enter__()
        second_handle = second.__enter__()
        first.__exit__(None, None, None)  # the older closes first
        second_handle.update(done=1)
        second.__exit__(None, None, None)
        outer.update("back")
    assert _lines(stream)[-3:] == [
        "Outer · two 1 (0s)",
        "Outer · back (0s)",
        "Outer: done · back (0s)",
    ]


def test_concurrent_inner_tasks_render_one_phase_and_one_message() -> None:
    stream = io.StringIO()
    with (
        _session(stream),
        _progress("Outer", verbose=True),
        _progress("a", verbose=True) as a,
        _progress("b", verbose=True) as b,
    ):
        b.update(done=1, total=4)
        a.update("x", done=2, total=5)  # not the innermost: its update is the text
        b.update(done=2)
    assert _lines(stream)[3:6] == [
        "Outer · b 1/4 (0s)",
        "Outer · b 1/4 · a 2/5 · x (0s)",
        "Outer · b 2/4 (0s)",
    ]


def test_a_straggler_renders_alone_with_its_end_line() -> None:
    stream = io.StringIO()
    with _session(stream) as clock:
        with _progress("Parent"):
            pass
        with _progress("Straggler") as p:
            p.update(done=1, total=2)
            clock.advance(2)
    assert _lines(stream) == ["Straggler 1/2 (2s)", "Straggler: done (2s)"]


def test_a_task_that_outlives_its_parent_becomes_the_line() -> None:
    stream = io.StringIO()
    with _session(stream) as clock:
        parent = _progress("Parent")
        parent.__enter__()
        child = _progress("Child")
        handle = child.__enter__()
        parent.__exit__(None, None, None)
        handle.update(done=1, total=2)
        clock.advance(2)
        child.__exit__(None, None, None)
    assert _lines(stream) == ["Child 1/2 (2s)", "Child: done (2s)"]


def test_two_threads_crossing_one_step_print_it_once() -> None:
    stream = io.StringIO()
    with _session(stream) as clock, _progress("T") as p:
        p.update(done=0, total=100)
        clock.advance(2)
        threads = [
            threading.Thread(target=p.update, kwargs={"done": 40}, name=f"step-{n}")
            for n in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(_JOIN)
            assert not thread.is_alive()
    assert sum(" 40/100 " in line for line in _lines(stream)) == 1


# --------------------------------------------------------------------------- #
# The ticker
# --------------------------------------------------------------------------- #


def test_one_ticker_runs_while_a_task_is_open() -> None:
    stream = io.StringIO()
    with _session(stream):
        assert not _ticker_alive()
        with _progress("A"), _progress("B"):
            names = [thread.name for thread in threading.enumerate()]
            assert names.count("untaped-progress") == 1
        _wait_for_no_ticker()
        with _progress("C"):  # a task opened after the join gets a ticker
            assert _ticker_alive()
    _wait_for_no_ticker()
    assert open_tasks() == 0


def test_a_task_outlives_its_scope_and_keeps_its_ticker() -> None:
    stream = io.StringIO()
    with progress_clock() as clock, redirect_stderr(stream):
        with entered(start_run()):
            task = current_sink().progress("Long")
            handle = task.__enter__()
        assert _ticker_alive()  # a scope's close stops nothing
        handle.update(done=1, total=2)
        clock.advance(2)
        task.__exit__(None, None, None)
    assert _lines(stream) == ["Long 1/2 (2s)", "Long: done (2s)"]
    _wait_for_no_ticker()


def test_a_tick_whose_write_logs_does_not_deadlock() -> None:
    class Logging(io.StringIO):
        def write(self, text: str) -> int:
            if text.startswith("Busy"):
                Diagnostics().warning("written while writing")  # re-entrant
            return super().write(text)

    stream = Logging()
    done = threading.Event()

    def run() -> None:
        with _session(stream) as clock, _progress("Busy"):
            clock.advance(2)
        done.set()

    thread = threading.Thread(target=run, name="reentrant")
    thread.start()
    thread.join(_JOIN)
    assert done.is_set()
    assert "warning: written while writing" in stream.getvalue()


def test_a_task_opened_while_the_ticker_is_joined_gets_its_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticking = threading.Event()
    release = threading.Event()

    def blocked_tick() -> None:
        ticking.set()
        assert release.wait(_JOIN)

    monkeypatch.setattr(sink, "tick", blocked_tick)
    stream = io.StringIO()
    with _session(stream):
        first = _progress("A")
        first.__enter__()
        assert ticking.wait(_JOIN)  # the ticker is inside a tick
        closing = threading.Thread(target=first.__exit__, args=(None, None, None), name="close-A")
        closing.start()  # stops the ticker, then joins it: blocked until the tick returns
        deadline = time.monotonic() + _JOIN
        while sink._ticker is not None and time.monotonic() < deadline:
            time.sleep(0.001)  # A's close has stopped the ticker and is joining it
        with _progress("C"):
            tickers = [t for t in threading.enumerate() if t.name == "untaped-progress"]
            assert len(tickers) == 2  # C's own, beside the one A's close is joining
            release.set()
        closing.join(_JOIN)
        assert not closing.is_alive()
    _wait_for_no_ticker()


def test_a_tick_that_raises_keeps_the_ticker(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def failing_tick() -> None:
        calls.append(1)
        if len(calls) == 1:
            raise ValueError("I/O operation on closed file")

    monkeypatch.setattr(sink, "tick", failing_tick)
    with _session(io.StringIO()), _progress("T"):
        deadline = time.monotonic() + _JOIN
        while len(calls) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
    assert len(calls) >= 2


def test_a_task_whose_open_line_fails_is_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    class Broken(io.StringIO):
        def write(self, text: str) -> int:
            raise BrokenPipeError

    with (
        pytest.raises(BrokenPipeError),
        _session(Broken(), monkeypatch, json_lines=True),
        _progress("T", verbose=True),  # the open line is written at once
    ):
        pass
    assert open_tasks() == 0
    _wait_for_no_ticker()


# --------------------------------------------------------------------------- #
# The hold
# --------------------------------------------------------------------------- #


def test_progress_is_dropped_under_a_hold_on_a_pipe() -> None:
    stream = io.StringIO()
    with _session(stream) as clock, _progress("T") as p:
        p.update(done=0, total=10)
        with current_sink().hold():
            clock.advance(2)  # the start line is due under the hold: dropped
            p.update(done=5)
        assert stream.getvalue() == ""
        clock.advance(0.1)  # after the hold the late start line comes, with today's state
    assert _lines(stream) == ["T 5/10 (2s)", "T: done (2s)"]


def test_the_end_line_of_a_started_task_is_held_not_dropped() -> None:
    stream = io.StringIO()
    with _session(stream) as clock:
        with _progress("T"):
            clock.advance(2)  # the start line prints
            with current_sink().hold():
                clock.advance(1)
                assert _lines(stream) == ["T (2s)"]
                hold = current_sink().hold()
                hold.__enter__()
        assert _lines(stream) == ["T (2s)"]  # the end line waits for the screen
        hold.__exit__(None, None, None)
    assert _lines(stream) == ["T (2s)", "T: done (3s)"]


def test_the_spinner_is_cleared_and_not_drawn_under_a_hold() -> None:
    stream = TtyStringIO()
    with _session(stream) as clock, _progress("Working"):
        assert "Working" in stream.getvalue()
        with current_sink().hold():
            before = stream.getvalue()
            assert before.endswith("\r")  # cleared for the screen
            clock.advance(1)
            assert stream.getvalue() == before  # the tick draws nothing
        clock.advance(0.1)
        assert stream.getvalue().rstrip().endswith("(1s)")  # back on the next tick


def test_json_progress_is_not_held(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = io.StringIO()
    with (
        _session(stream, monkeypatch, json_lines=True) as clock,
        _progress("T"),
        current_sink().hold(),
    ):
        clock.advance(2)
        assert json.loads(stream.getvalue())["kind"] == "untaped.progress"


# --------------------------------------------------------------------------- #
# Tests through untaped.sdk.testing
# --------------------------------------------------------------------------- #


def _app(body: object) -> App:
    app = App(name="demo")

    @app.default
    def run() -> None:
        with Diagnostics().progress("Working") as handle:
            body(handle)  # type: ignore[operator]

    return app


def test_invoke_cli_freezes_the_clock() -> None:
    def body(handle: ProgressHandle) -> None:
        handle.update(done=1, total=2)
        time.sleep(2.1)  # the one real sleep: wall time passes, the frozen clock does not

    result = invoke_cli(_app(body))
    assert result.exit_code == 0
    assert not [d for d in result.diagnostics if isinstance(d, Progress)]


def test_a_test_advances_the_clock_from_inside_the_command() -> None:
    with progress_clock() as clock:

        def body(handle: ProgressHandle) -> None:
            handle.update(phase="fetching", done=1, total=2)
            clock.advance(2)

        result = invoke_cli(_app(body))
    lines = [d for d in result.diagnostics if isinstance(d, Progress)]
    assert [(d.phase, d.done, d.state) for d in lines] == [
        ("fetching", 1, "running"),
        (None, None, "done"),
    ]


def test_ctrl_c_inside_a_task_ends_it_failed_and_exits_130() -> None:
    with progress_clock() as clock:

        def body(handle: ProgressHandle) -> None:
            clock.advance(2)
            raise KeyboardInterrupt

        result = invoke_cli(_app(body))
    assert result.exit_code == 130
    assert [d.state for d in result.diagnostics if isinstance(d, Progress)] == [
        "running",
        "failed",
    ]


def test_a_failure_inside_a_task_is_a_failure_line() -> None:
    def body(handle: ProgressHandle) -> None:
        Diagnostics().failure(UntapedError("it broke"), item="acme/x")

    result = invoke_cli(_app(body))
    assert [type(d) for d in result.diagnostics] == [Failure]


def test_no_progress_does_nothing() -> None:
    NO_PROGRESS.update("x", phase="p", done=1, total=2)
    NO_PROGRESS.update()


# --------------------------------------------------------------------------- #
# The spinner on a terminal (real time: the ticker animates it)
# --------------------------------------------------------------------------- #


def test_the_spinner_animates_while_the_main_thread_blocks() -> None:
    stream = TtyStringIO()

    def frames() -> set[str]:
        return {ch for ch in stream.getvalue() if ch in _BRAILLE}

    with redirect_stderr(stream), entered(start_run()), _progress("Work"):
        deadline = time.monotonic() + _JOIN
        while len(frames()) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
    assert len(frames()) >= 2
    _wait_for_no_ticker()


def test_the_spinner_line_composes_and_clears_on_exit() -> None:
    stream = TtyStringIO()
    with _session(stream), _progress("Installing") as p:
        p.update("almost", phase="unpacking", done=3, total=4)
    output = stream.getvalue()
    assert "Installing · unpacking 3/4 · almost (0s)" in output
    assert output.endswith("\r")


def test_the_spinner_draws_the_nested_task_as_the_phase() -> None:
    stream = TtyStringIO()
    with _session(stream), _progress("Outer"), _progress("Inner") as inner:
        inner.update(done=1, total=2)
        assert stream.getvalue().split("\r")[-1].endswith("Outer · Inner 1/2 (0s)")


def test_a_line_emitted_under_the_spinner_clears_it_then_redraws() -> None:
    stream = TtyStringIO()
    with _session(stream), _progress("Working"):
        Diagnostics().warning("x: slow")
        after = stream.getvalue()
    head, _, tail = after.partition("warning: x: slow\n")
    assert head.endswith("\r")
    assert "Working" in tail


def test_the_spinner_clears_when_the_block_raises() -> None:
    stream = TtyStringIO()
    with pytest.raises(ValueError, match="boom"), _session(stream), _progress("Working"):
        raise ValueError("boom")
    assert stream.getvalue().endswith("\r")


class _AsciiTty(io.StringIO):
    encoding = "ascii"

    def isatty(self) -> bool:
        return True


def test_the_spinner_falls_back_to_ascii_frames() -> None:
    stream = _AsciiTty()
    with _session(stream), _progress("Working"):
        pass
    out = stream.getvalue()
    assert any(ch in out for ch in "|/-\\")
    assert not any(ch in out for ch in _BRAILLE)


def test_the_spinner_line_is_truncated_to_the_terminal_width(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLUMNS", "30")
    stream = TtyStringIO()
    with _session(stream), _progress("x" * 200):
        pass
    drawn = [chunk for chunk in stream.getvalue().split("\r") if chunk.strip()]
    assert drawn
    assert all(len(chunk) < 30 for chunk in drawn)


def test_quiet_draws_no_spinner() -> None:
    stream = TtyStringIO()
    with _session(stream), _progress("Working", quiet=True) as p:
        p.update(done=1, total=2)
    assert stream.getvalue() == ""


def test_verbose_on_a_terminal_prints_lines_not_a_spinner() -> None:
    stream = TtyStringIO()
    with _session(stream), _progress("Resolving", verbose=True) as p:
        p.update("downloading")
    assert "\r" not in stream.getvalue()
    assert "Resolving · downloading (0s)" in _lines(stream)
