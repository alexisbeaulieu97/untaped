"""In-place stderr progress reporting for long-running operations.

Three rendering modes share one ``update`` contract:

* a TTY spinner animated by a background thread, so the line stays alive while
  the main thread blocks in a subprocess, file lock, or network call;
* throttled newline-terminated lines for non-TTY/piped output, so CI logs stay
  readable;
* verbose passthrough that announces the label and otherwise stays out of the
  way, letting the wrapped tool's own output stream through.

The public surface is :func:`progress_reporter` and the :class:`ProgressHandle`
protocol it yields. Callers get the handle from ``UiContext.progress``; only the
context manager drives the start/finish lifecycle.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Protocol, TextIO

_THROTTLE_INTERVAL = 2.0
_PERCENT_STEP = 10
_SPINNER_TICK = 0.1
_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_ASCII_SPINNER_FRAMES = "|/-\\"


def _spinner_frames(stream: TextIO) -> str:
    """Braille frames on a UTF-8 stream, ASCII frames otherwise.

    An unknown encoding (e.g. a ``StringIO`` with no ``encoding`` attribute) is
    assumed UTF-8; only a stream that *declares* a non-UTF encoding (a legacy
    ``LANG=C`` terminal) falls back to ASCII so the spinner doesn't render as
    replacement boxes.
    """
    encoding = (getattr(stream, "encoding", None) or "").lower()
    if encoding and "utf" not in encoding:
        return _ASCII_SPINNER_FRAMES
    return _SPINNER_FRAMES


class ProgressHandle(Protocol):
    """What a wrapped operation calls to report progress."""

    def update(
        self, message: str, *, fraction: float | None = None, new_phase: bool = False
    ) -> None:
        """Report progress.

        ``fraction`` is completion in ``[0, 1]`` when known. ``new_phase`` marks
        the start of a distinct phase so non-TTY output emits its first line
        immediately instead of waiting out the throttle window.
        """
        ...


class _Backend(Protocol):
    def start(self) -> None: ...

    def update(
        self, message: str, *, fraction: float | None = None, new_phase: bool = False
    ) -> None: ...

    def finish(self, *, failed: bool) -> None: ...


class _SilentHandle:
    """No-op progress for ``--quiet``: emits nothing on any stream."""

    def start(self) -> None:
        return None

    def update(
        self, message: str, *, fraction: float | None = None, new_phase: bool = False
    ) -> None:
        return None

    def finish(self, *, failed: bool) -> None:
        return None


class _VerboseHandle:
    """Announce the label, then let the wrapped tool's output stream through."""

    def __init__(self, label: str, *, stream: TextIO) -> None:
        self._label = label
        self._stream = stream

    def start(self) -> None:
        self._stream.write(f"{self._label}\n")
        self._stream.flush()

    def update(
        self, message: str, *, fraction: float | None = None, new_phase: bool = False
    ) -> None:
        self._stream.write(f"{message}\n")
        self._stream.flush()

    def finish(self, *, failed: bool) -> None:
        return None


class _ThrottledHandle:
    """Newline-terminated, throttled progress for non-TTY/piped streams."""

    def __init__(self, label: str, *, stream: TextIO) -> None:
        self._label = label
        self._stream = stream
        self._last_emit: float | None = None
        self._last_step = -1

    def start(self) -> None:
        self._emit(self._label)

    def update(
        self, message: str, *, fraction: float | None = None, new_phase: bool = False
    ) -> None:
        if new_phase:
            self._last_emit = None
            self._last_step = -1
        step = self._last_step
        if fraction is not None:
            step = int(min(max(fraction, 0.0), 1.0) * 100) // _PERCENT_STEP
        now = time.monotonic()
        due = self._last_emit is None or now - self._last_emit >= _THROTTLE_INTERVAL
        if not due and step <= self._last_step:
            return
        self._last_step = max(step, self._last_step)
        self._emit(message)

    def finish(self, *, failed: bool) -> None:
        return None

    def _emit(self, message: str) -> None:
        self._last_emit = time.monotonic()
        self._stream.write(f"{message}\n")
        self._stream.flush()


class _SpinnerHandle:
    """In-place animated spinner for TTY stderr, driven by a background thread."""

    def __init__(self, label: str, *, stream: TextIO) -> None:
        self._label = label
        self._stream = stream
        self._frames = _spinner_frames(stream)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._frame = 0
        self._line_width = 0
        self._started_at = time.monotonic()

    def start(self) -> None:
        with self._lock:
            self._draw()
        self._thread.start()

    def update(
        self, message: str, *, fraction: float | None = None, new_phase: bool = False
    ) -> None:
        with self._lock:
            self._label = message
            self._draw()

    def finish(self, *, failed: bool) -> None:
        self._stop.set()
        self._thread.join()
        with self._lock:
            self._stream.write("\r" + " " * self._line_width + "\r")
            self._stream.flush()

    def _run(self) -> None:
        while not self._stop.wait(_SPINNER_TICK):
            with self._lock:
                self._draw()

    def _draw(self) -> None:
        frame = self._frames[self._frame % len(self._frames)]
        self._frame += 1
        elapsed = int(time.monotonic() - self._started_at)
        line = f"{frame} {self._label} ({elapsed}s)"
        self._stream.write("\r" + line.ljust(self._line_width))
        self._stream.flush()
        self._line_width = len(line)


def _make_handle(
    label: str, *, stream: TextIO, verbose: bool, quiet: bool, isatty: bool
) -> _Backend:
    if quiet:
        return _SilentHandle()
    if verbose:
        return _VerboseHandle(label, stream=stream)
    if isatty:
        return _SpinnerHandle(label, stream=stream)
    return _ThrottledHandle(label, stream=stream)


@contextmanager
def progress_reporter(
    label: str, *, stream: TextIO, verbose: bool, quiet: bool = False, isatty: bool
) -> Iterator[ProgressHandle]:
    """Report progress for a blocking operation on ``stream`` (typically stderr).

    Yields a :class:`ProgressHandle`; the rendering mode is chosen from
    ``quiet``/``verbose``/``isatty`` (``quiet`` wins — it stays fully silent).
    The spinner line (TTY) is cleared on exit — success or failure — so any
    propagating error prints cleanly.
    """
    handle = _make_handle(label, stream=stream, verbose=verbose, quiet=quiet, isatty=isatty)
    handle.start()
    failed = False
    try:
        yield handle
    except BaseException:
        failed = True
        raise
    finally:
        handle.finish(failed=failed)
