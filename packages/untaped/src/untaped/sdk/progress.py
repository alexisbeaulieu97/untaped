"""The handle a wrapped operation reports progress through, and nothing that renders it.

A command opens a task with ``ctx.diag.progress(label)`` and passes the
handle it yields to the layer that does the work; the sink
(``untaped.runtime.sink``) decides what becomes output. An inner layer takes
``progress: ProgressHandle = NO_PROGRESS`` and never checks for ``None``.
This module imports nothing from the sink.
"""

from __future__ import annotations

from typing import Final, Protocol


class ProgressHandle(Protocol):
    """What a wrapped operation calls to report progress."""

    def update(
        self,
        message: str | None = None,
        *,
        phase: str | None = None,
        done: int | None = None,
        total: int | None = None,
    ) -> None:
        """Report the current state of the task.

        ``message`` is free text about this update (``"4 changed"``, the
        current item); ``None`` clears the last one, so a count-only update
        never shows stale text. ``phase`` names the current phase: a name
        different from the current one starts a phase and resets ``done``
        and ``total`` to what this call passes. ``done`` and ``total`` are
        absolute counts, never a fraction. ``phase``, ``done`` and ``total``
        keep their value when passed ``None``.
        """
        ...


class _NoProgress:
    """The handle that reports nothing; stateless, so one instance is shared."""

    def update(
        self,
        message: str | None = None,
        *,
        phase: str | None = None,
        done: int | None = None,
        total: int | None = None,
    ) -> None:
        return None


NO_PROGRESS: Final[ProgressHandle] = _NoProgress()
"""The do-nothing handle: an inner layer's default, so it never branches on ``None``."""
