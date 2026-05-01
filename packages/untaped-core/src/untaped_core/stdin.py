"""Stdin helpers for piping values into commands."""

from __future__ import annotations

import sys

from untaped_core.errors import ConfigError


def read_stdin() -> list[str]:
    """Read newline-separated values from stdin.

    Returns an empty list if stdin is a tty (interactive) — never blocks
    waiting for user input. Empty lines are skipped; surrounding whitespace
    on each line is stripped.
    """
    if sys.stdin.isatty():
        return []
    return [stripped for line in sys.stdin if (stripped := line.strip())]


def read_identifiers(positional: list[str], *, stdin: bool) -> list[str]:
    """Resolve identifiers from positional args or stdin (exactly one).

    Used by every CLI command that takes a list of names/IDs to honour
    the documented pipeline shape (``list --format raw | get --stdin``).
    Mixing positional + ``--stdin`` is refused: a misplaced flag would
    silently act on the wrong set. Empty input on either side is also
    refused so commands don't no-op when given nothing to do.
    """
    if stdin and positional:
        raise ConfigError("provide identifiers as positional args or via --stdin, not both")
    if stdin:
        ids = read_stdin()
        if not ids:
            raise ConfigError("no identifiers received on stdin")
        return ids
    if not positional:
        raise ConfigError("at least one identifier is required (or use --stdin)")
    return positional
