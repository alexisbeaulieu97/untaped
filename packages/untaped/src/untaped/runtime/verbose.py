"""``--verbose`` for one CLI invocation, and logging through the stderr sink.

``--verbose``/``-v`` is a core root option whose handler is :func:`enable`. The
flag lives on the run's output policy (``untaped.sdk._run.Run.policy``): it
streams a wrapped tool's output live instead of capturing it, and raises the
``untaped`` logger to DEBUG. The logger's handler forwards every record to the
sink as an ``untaped.debug`` diagnostic (``DEBUG untaped.http: …`` in text, a
JSON line under JSON diagnostics), with credentials masked
(:func:`~untaped.runtime.redaction.redact_credentials`). The flag ends with its
run; the logger's level is process-wide, so the end of a verbose run
(``untaped.runtime.run.entered``) calls :func:`restore`, which sets it from the
run current then, and the DEBUG level never leaks across in-process callers
(tests, embedding). The handler added by :func:`configure_logging` is left
attached (idempotent), but receives nothing once the level is restored.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING

from untaped.runtime.redaction import redact_credentials
from untaped.runtime.sink import current_sink
from untaped.sdk._run import is_verbose
from untaped.sdk.diagnostic import Debug

if TYPE_CHECKING:
    from untaped.sdk._run import Run

_LOGGER_NAME = "untaped"


def enable(run: Run, _value: str = "") -> None:
    """Root-option handler for ``--verbose``/``-v``, onto ``run``, the invocation's.

    Takes no value; the token pass passes a placeholder string that is ignored.
    Turns on verbose output for the run and routes DEBUG logs to stderr.
    """
    run.set_policy(replace(run.policy, verbose=True))
    configure_logging(logging.DEBUG)


def restore() -> None:
    """Set the ``untaped`` logger's level from the current run's flag (none: off)."""
    enabled = is_verbose()
    level = logging.DEBUG if enabled else logging.NOTSET
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = not enabled


class _SinkHandler(logging.Handler):
    """Forward each log record, credentials masked, to the stderr sink as ``untaped.debug``."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            debug = Debug(logger=record.name, message=redact_credentials(record.getMessage()))
            current_sink().emit(debug, rendered=redact_credentials(self.format(record)))
        except Exception:
            self.handleError(record)


def configure_logging(level: int | str) -> None:
    """Route the ``untaped`` logger to the stderr sink at ``level`` (idempotent).

    stdout stays data-only; logs go to stderr like every other diagnostic.
    """
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False
    if not any(isinstance(handler, _SinkHandler) for handler in logger.handlers):
        handler = _SinkHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        logger.addHandler(handler)


__all__ = ["configure_logging", "enable", "restore"]
