"""Loguru-based logging configured to stderr only.

Logs go to stderr so that command stdout stays clean for piping into
other tools (`untaped awx list --format raw | fzf`).
"""

from __future__ import annotations

import sys
from typing import Any

from loguru import logger

_CONFIGURED = False


def configure_logging(level: str = "INFO") -> None:
    """Configure the loguru sink to write to stderr at the given level.

    Idempotent: calling multiple times replaces previous handlers.
    """
    global _CONFIGURED
    logger.remove()
    logger.add(
        sys.stderr,
        level=level,
        format="<level>{level: <8}</level> | <cyan>{name}</cyan> | {message}",
        colorize=True,
    )
    _CONFIGURED = True


def get_logger(name: str) -> Any:
    """Return a loguru logger bound with the given name.

    Lazily configures the default sink if `configure_logging` has not yet
    been called.
    """
    if not _CONFIGURED:
        configure_logging()
    return logger.bind(name=name)
