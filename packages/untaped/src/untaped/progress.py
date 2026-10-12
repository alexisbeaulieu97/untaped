"""Progress reporting for long-running operations: the handle and its line backends.

Three rendering modes share one ``update`` contract:

* a TTY spinner animated by a background thread, so the line stays alive while
  the main thread blocks in a subprocess, file lock, or network call (the
  sink's, ``untaped.output.sink``, since it shares the sink's lock);
* throttled newline-terminated lines for non-TTY/piped output, so CI logs stay
  readable;
* verbose passthrough that announces the label and otherwise stays out of the
  way, letting the wrapped tool's own output stream through.

Every backend writes through the sink, so a line printed while progress is up
never garbles it. Callers get the handle from ``UiContext.progress``; only the
context manager drives the start/finish lifecycle.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Protocol

_THROTTLE_INTERVAL = 2.0
_PERCENT_STEP = 10


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

    def log(self, line: str) -> None:
        """Print ``line`` (e.g. an ``error:`` line) on the progress stream.

        A TTY spinner is cleared first and redrawn after, so the line is never
        garbled by the in-place repaint. Prints even under ``--quiet``.
        """
        ...


class Backend(Protocol):
    """What renders one progress block."""

    def start(self) -> None: ...

    def update(
        self, message: str, *, fraction: float | None = None, new_phase: bool = False
    ) -> None: ...

    def log(self, line: str) -> None: ...

    def finish(self, *, failed: bool) -> None: ...


class _SilentHandle:
    """Progress for ``--quiet``: no progress output; logged lines still print."""

    def __init__(self, *, write: Callable[[str], None]) -> None:
        self._write = write

    def log(self, line: str) -> None:
        self._write(f"{line}\n")

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

    def __init__(self, label: str, *, write: Callable[[str], None]) -> None:
        self._label = label
        self._write = write

    def start(self) -> None:
        self._write(f"{self._label}\n")

    def log(self, line: str) -> None:
        self._write(f"{line}\n")

    def update(
        self, message: str, *, fraction: float | None = None, new_phase: bool = False
    ) -> None:
        self._write(f"{message}\n")

    def finish(self, *, failed: bool) -> None:
        return None


class _ThrottledHandle:
    """Newline-terminated, throttled progress for non-TTY/piped streams."""

    def __init__(self, label: str, *, write: Callable[[str], None]) -> None:
        self._label = label
        self._write = write
        self._last_emit: float | None = None
        self._last_step = -1

    def start(self) -> None:
        self._emit(self._label)

    def log(self, line: str) -> None:
        self._write(f"{line}\n")

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
        self._write(f"{message}\n")


def _make_handle(
    label: str,
    *,
    write: Callable[[str], None],
    verbose: bool,
    quiet: bool,
    spinner: Callable[[], Backend] | None,
) -> Backend:
    if quiet:
        return _SilentHandle(write=write)
    if verbose:
        return _VerboseHandle(label, write=write)
    if spinner is not None:
        return spinner()
    return _ThrottledHandle(label, write=write)


@contextmanager
def progress_reporter(
    label: str,
    *,
    write: Callable[[str], None],
    verbose: bool,
    quiet: bool = False,
    spinner: Callable[[], Backend] | None = None,
) -> Iterator[ProgressHandle]:
    """Report progress for a blocking operation, writing lines through ``write``.

    Yields a :class:`ProgressHandle`; the rendering mode is chosen from
    ``quiet``/``verbose`` and ``spinner``, the terminal's spinner backend
    (``quiet`` wins: it stays fully silent). The spinner line is cleared on
    exit, success or failure, so any propagating error prints cleanly.
    """
    handle = _make_handle(label, write=write, verbose=verbose, quiet=quiet, spinner=spinner)
    handle.start()
    failed = False
    try:
        yield handle
    except BaseException:
        failed = True
        raise
    finally:
        handle.finish(failed=failed)
