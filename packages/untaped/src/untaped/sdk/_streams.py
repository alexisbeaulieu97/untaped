"""Whether a stream is a terminal: the one check the SDK and the run share."""

from __future__ import annotations

from typing import TextIO


def stream_is_tty(stream: TextIO) -> bool:
    """Whether ``stream`` is a terminal; a closed, broken or plain stream is not."""
    isatty = getattr(stream, "isatty", None)
    if not callable(isatty):
        return False
    try:
        return bool(isatty())
    except OSError, ValueError:  # ValueError: a closed stream
        return False
