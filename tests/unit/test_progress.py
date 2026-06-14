"""Unit tests for stderr progress reporting."""

from __future__ import annotations

import io

import pytest

from untaped.progress import progress_reporter


class TtyStringIO(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_non_tty_progress_emits_label_and_phase_updates_as_lines() -> None:
    stream = io.StringIO()

    with progress_reporter("Resolving deps", stream=stream, verbose=False, isatty=False) as p:
        p.update("step one", new_phase=True)
        p.update("step two", new_phase=True)

    output = stream.getvalue()
    assert "Resolving deps" in output
    assert "step one" in output
    assert "step two" in output
    # Non-TTY output is newline-terminated lines, never in-place carriage returns.
    assert "\r" not in output


def test_non_tty_progress_throttles_unmarked_updates() -> None:
    stream = io.StringIO()

    with progress_reporter("Working", stream=stream, verbose=False, isatty=False) as p:
        # Rapid updates with no phase change and no fraction step are throttled
        # so piped/CI logs stay readable.
        p.update("tick")
        p.update("tick")
        p.update("tick")

    # The initial label always shows; the throttled ticks collapse.
    assert stream.getvalue().count("tick") <= 1


def test_tty_progress_animates_on_stderr_and_clears_line_on_exit() -> None:
    stream = TtyStringIO()

    with progress_reporter("Installing", stream=stream, verbose=False, isatty=True) as p:
        p.update("almost there")

    output = stream.getvalue()
    assert "Installing" in output or "almost there" in output
    # In-place rendering uses carriage returns and clears the line on exit so
    # following output starts clean.
    assert "\r" in output
    assert output.endswith("\r")


def test_verbose_progress_passes_messages_through_without_animation() -> None:
    stream = io.StringIO()

    with progress_reporter("Resolving deps", stream=stream, verbose=True, isatty=True) as p:
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
        progress_reporter("Working", stream=stream, verbose=False, isatty=True),
    ):
        raise ValueError("boom")

    # The spinner line is still cleared so the propagating error prints cleanly.
    assert stream.getvalue().endswith("\r")
