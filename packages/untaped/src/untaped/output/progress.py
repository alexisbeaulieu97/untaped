"""The handle a wrapped operation reports progress through, and nothing that renders it.

A command opens a task with ``ctx.diag.progress(label)`` and passes the
handle it yields to the layer that does the work; the sink
(``untaped.output.sink``) decides what becomes output. This module imports
nothing from the sink.
"""

from __future__ import annotations

from typing import Protocol


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
