"""Process-scoped verbose state and stderr logging for one CLI invocation.

``--verbose``/``-v`` is a core root option whose handler is :func:`enable`. The
flag is read by ``UiContext`` to stream a wrapped tool's output live instead of
capturing it, and it raises the ``untaped`` logger to DEBUG on stderr. The root
callback calls :func:`reset` after each invocation, clearing the flag and the
DEBUG level so neither leaks across in-process callers (tests, embedding). The
stderr handler added by :func:`configure_logging` is left attached (idempotent),
but emits nothing once the level is restored.
"""

from __future__ import annotations

import logging
import sys

_LOGGER_NAME = "untaped"

_verbose = False


def enable(_value: str = "") -> None:
    """Root-option handler for ``--verbose``/``-v``.

    Takes no value; the dispatcher passes a placeholder string that is ignored.
    Turns on verbose output for this invocation and routes DEBUG logs to stderr.
    """
    global _verbose
    _verbose = True
    configure_logging(logging.DEBUG)


def is_verbose() -> bool:
    """Whether ``--verbose`` is active for the current invocation."""
    return _verbose


def reset() -> None:
    """Clear verbose state and the DEBUG level so neither leaks past one invocation."""
    global _verbose
    _verbose = False
    logging.getLogger(_LOGGER_NAME).setLevel(logging.NOTSET)


def configure_logging(level: int | str) -> None:
    """Route the ``untaped`` logger to stderr at ``level`` (idempotent).

    stdout stays data-only; logs go to stderr like every other diagnostic.
    """
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False
    if not any(
        isinstance(handler, logging.StreamHandler) and handler.stream is sys.stderr
        for handler in logger.handlers
    ):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        logger.addHandler(handler)
