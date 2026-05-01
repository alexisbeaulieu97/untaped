"""Stdin helpers for piping values into commands."""

from __future__ import annotations

import sys


def read_stdin() -> list[str]:
    """Read newline-separated values from stdin.

    Returns an empty list if stdin is a tty (interactive) — never blocks
    waiting for user input. Empty lines are skipped; surrounding whitespace
    on each line is stripped.
    """
    if sys.stdin.isatty():
        return []
    return [stripped for line in sys.stdin if (stripped := line.strip())]
