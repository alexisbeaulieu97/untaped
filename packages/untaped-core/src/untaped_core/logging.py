"""Logging helpers using Loguru."""

from __future__ import annotations

import sys
from typing import Literal

from loguru import logger


def configure_logging(*, level: str = "INFO", json_output: bool = False) -> None:
    """Configure Loguru with a consistent format for the CLI and services."""

    logger.remove()
    logger.add(
        sys.stderr,
        level=level.upper(),
        serialize=json_output,
        format=(
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {message}"
            if not json_output
            else None
        ),
    )


def get_logger() -> "logger":  # type: ignore[return-value]
    """Return the configured Loguru logger."""

    return logger
