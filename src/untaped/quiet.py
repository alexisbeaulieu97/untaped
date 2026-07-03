"""Process-scoped quiet state for one CLI invocation.

``--quiet``/``-q`` is the inverse of ``--verbose``: it mutes the progress
spinner and semantic ``success``/``info`` messages while leaving
``warning``/``error``, interactive prompts, data on stdout, and destructive
confirmation previews untouched. The handler is :func:`enable`; the root
callback calls :func:`reset` after each invocation so the flag never leaks
across in-process callers (tests, embedding).
"""

from __future__ import annotations

from contextvars import ContextVar

_quiet: ContextVar[bool] = ContextVar("untaped_quiet", default=False)


def enable(_value: str = "") -> None:
    """Root-option handler for ``--quiet``/``-q``.

    Takes no value; the dispatcher passes a placeholder string that is ignored.
    """
    _quiet.set(True)


def is_quiet() -> bool:
    """Whether ``--quiet`` is active for the current invocation."""
    return _quiet.get()


def reset() -> None:
    """Clear quiet state so it doesn't leak past one invocation."""
    _quiet.set(False)
