"""Unit tests for stderr progress reporting."""

from __future__ import annotations

import io
import time
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stderr

import pytest

from untaped.output.progress import ProgressHandle
from untaped.output.sink import Diagnostics, current_sink

_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


@contextmanager
def _progress(
    label: str, *, stream: io.StringIO, verbose: bool, quiet: bool = False
) -> Iterator[ProgressHandle]:
    """Progress through the sink, onto ``stream`` (a terminal when it says it is one)."""
    with redirect_stderr(stream), current_sink().progress(label, verbose=verbose, quiet=quiet) as p:
        yield p


class TtyStringIO(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_non_tty_progress_emits_label_and_phase_updates_as_lines() -> None:
    stream = io.StringIO()

    with _progress("Resolving deps", stream=stream, verbose=False) as p:
        p.update(phase="step one")
        p.update(phase="step two")

    output = stream.getvalue()
    assert "Resolving deps" in output
    assert "step one" in output
    assert "step two" in output
    # Non-TTY output is newline-terminated lines, never in-place carriage returns.
    assert "\r" not in output


def test_non_tty_progress_throttles_unmarked_updates() -> None:
    stream = io.StringIO()

    with _progress("Working", stream=stream, verbose=False) as p:
        # Rapid updates with no phase change and no fraction step are throttled
        # so piped/CI logs stay readable.
        p.update("tick")
        p.update("tick")
        p.update("tick")

    # The initial label always shows; the throttled ticks collapse.
    assert stream.getvalue().count("tick") <= 1


def test_non_tty_progress_emits_on_a_count_step_within_throttle_window() -> None:
    stream = io.StringIO()

    # All updates happen within the throttle window, so only 10 % steps of
    # done/total break through; same-step updates collapse.
    with _progress("Work", stream=stream, verbose=False) as p:
        p.update("ten", done=10, total=100)
        p.update("fifty", done=50, total=100)
        p.update("fifty-five", done=55, total=100)

    output = stream.getvalue()
    assert "ten" in output
    assert "fifty" in output
    assert "fifty-five" not in output


def test_tty_spinner_animates_while_the_main_thread_blocks() -> None:
    stream = TtyStringIO()

    def frames() -> set[str]:
        return {ch for ch in stream.getvalue() if ch in _SPINNER_FRAMES}

    with _progress("Work", stream=stream, verbose=False):
        # No update() calls: only the background thread can advance the frame.
        deadline = time.monotonic() + 5
        while len(frames()) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)

    assert len(frames()) >= 2


def test_tty_progress_animates_on_stderr_and_clears_line_on_exit() -> None:
    stream = TtyStringIO()

    with _progress("Installing", stream=stream, verbose=False) as p:
        p.update("almost there")

    output = stream.getvalue()
    assert "Installing" in output or "almost there" in output
    # In-place rendering uses carriage returns and clears the line on exit so
    # following output starts clean.
    assert "\r" in output
    assert output.endswith("\r")


def test_verbose_progress_passes_messages_through_without_animation() -> None:
    stream = io.StringIO()

    with _progress("Resolving deps", stream=stream, verbose=True) as p:
        p.update("downloading")

    output = stream.getvalue()
    assert "Resolving deps" in output
    assert "downloading" in output
    # Verbose mode never animates: no spinner carriage returns.
    assert "\r" not in output


def test_progress_reraises_exception_from_block() -> None:
    stream = TtyStringIO()

    with (
        pytest.raises(ValueError, match="boom"),
        _progress("Working", stream=stream, verbose=False),
    ):
        raise ValueError("boom")

    # The spinner line is still cleared so the propagating error prints cleanly.
    assert stream.getvalue().endswith("\r")


def test_progress_reporter_is_silent_when_quiet() -> None:
    """``--quiet`` mutes progress entirely, even on a TTY."""
    stream = TtyStringIO()
    with _progress("Working", stream=stream, verbose=False, quiet=True) as handle:
        handle.update("step", done=1, total=2)
    assert stream.getvalue() == ""


class AsciiTty(io.StringIO):
    encoding = "ascii"

    def isatty(self) -> bool:
        return True


def test_spinner_falls_back_to_ascii_frames_on_non_utf8_stream() -> None:
    """A stream declaring a non-UTF encoding gets ASCII spinner frames, not braille."""
    stream = AsciiTty()
    with _progress("Working", stream=stream, verbose=False):
        pass
    out = stream.getvalue()
    assert any(ch in out for ch in "|/-\\")
    assert not any(ch in out for ch in _SPINNER_FRAMES)


def test_a_line_emitted_under_the_spinner_clears_it_then_redraws() -> None:
    stream = TtyStringIO()
    with _progress("Working", stream=stream, verbose=False):
        Diagnostics().warning("x: slow")
        after_line = stream.getvalue()
    head, _, tail = after_line.partition("warning: x: slow\n")
    assert head.endswith("\r")  # the spinner line was cleared first
    assert "Working" in tail  # and redrawn after the line


def test_spinner_line_is_truncated_to_terminal_width(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLUMNS", "30")
    stream = TtyStringIO()
    with _progress("x" * 200, stream=stream, verbose=False):
        pass
    drawn = [chunk for chunk in stream.getvalue().split("\r") if chunk.strip()]
    assert drawn
    assert all(len(chunk) < 30 for chunk in drawn)
