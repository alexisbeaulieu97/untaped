"""Stdin helpers for piping values into commands."""

from __future__ import annotations

import json
import sys
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from untaped.runtime.render import stream_is_tty
from untaped.sdk._pipe import PipeEnvelope, is_envelope_line, parse_envelope_line
from untaped.sdk.errors import ConfigError, UsageError
from untaped.sdk.words import q


def stdin_is_terminal() -> bool:
    """Whether stdin is a terminal, so a read would wait for typing."""
    return stream_is_tty(sys.stdin)


def read_stdin() -> list[str]:
    """Read newline-separated values from stdin.

    Returns an empty list if stdin is a tty (interactive) — never blocks
    waiting for user input. Empty lines are skipped; surrounding whitespace
    on each line is stripped.
    """
    if sys.stdin.isatty():
        return []
    return [stripped for line in sys.stdin if (stripped := line.strip())]


def _trim_terminal_newline(text: str) -> str:
    if text.endswith("\r\n"):
        return text[:-2]
    if text.endswith("\n"):
        return text[:-1]
    return text


def read_stdin_text() -> str:
    """Read stdin as one raw text block (the dual of line-oriented ``read_stdin``).

    Returns ``""`` on a TTY (never blocks). Interior blank lines and
    formatting survive verbatim — this is for multi-line bodies that
    ``read_stdin``'s strip-and-split would mangle. Exactly one terminal
    newline (LF or CRLF) is trimmed: it belongs to the pipe, not the text.
    """
    if sys.stdin.isatty():
        return ""
    return _trim_terminal_newline(sys.stdin.read())


def read_stdin_line(noun: str) -> str:
    """Read one value piped on a single line, such as a token or a setting.

    The line ending is trimmed and the rest kept as typed. Nothing piped, a
    blank line or more than one line is an invalid value naming ``noun``.
    """
    text = read_stdin_text().rstrip("\r\n")
    if "\n" in text or "\r" in text:
        raise ConfigError(f"stdin should hold the {noun} on one line", category="invalid")
    if not text.strip():
        raise ConfigError(f"no {noun} received on stdin", category="invalid")
    return text


def _read_raw_lines() -> list[tuple[int, str]] | None:
    """Read stdin as ``(1-based line number, stripped line)`` pairs.

    Returns ``None`` when stdin is a terminal (never blocks), so callers can
    tell "nothing piped" from an empty pipe (``[]``). Blank lines are skipped
    but the line numbers track the original physical line, so envelope parse
    errors point at the right place.
    """
    if stream_is_tty(sys.stdin):
        return None
    pairs: list[tuple[int, str]] = []
    for lineno, line in enumerate(sys.stdin, start=1):
        stripped = line.strip()
        if stripped:
            pairs.append((lineno, stripped))
    return pairs


@dataclass(frozen=True)
class StdinInput:
    """Everything piped on stdin: bare values *or* pipe envelopes (never both).

    ``records`` is ``None`` for bare newline-separated input (``values`` holds
    the lines) and the parsed envelopes for an untaped ``--format pipe``
    stream (``values`` is then empty).
    """

    values: tuple[str, ...]
    records: tuple[PipeEnvelope, ...] | None


def read_stdin_input(
    *,
    accept_kinds: Collection[str] | None = None,
    allow_empty: bool = False,
) -> StdinInput:
    """Read stdin as bare values or a pipe stream, detected from the first line.

    ``Stdin``'s reader: a name/ID list or another command's ``--format pipe``
    output. Raises :class:`ConfigError` on empty stdin (``nothing received on
    stdin``), on mixed bare/envelope input, and on malformed envelopes
    (line-precise). With ``allow_empty`` an empty (or blank) pipe is no values
    instead, for commands where "nothing piped" means "nothing to do"; a
    terminal stdin (nothing piped at all) still raises. An envelope whose
    ``kind`` is set and is none of ``accept_kinds`` is a :class:`UsageError`
    (exit 2), so ``awx hosts list -f pipe | awx jobs get --stdin`` can never
    treat host IDs as job IDs; records without a kind are accepted.
    """
    pairs = _read_raw_lines()
    if not pairs:
        if pairs is None or not allow_empty:
            raise ConfigError("nothing received on stdin", category="invalid")
        return StdinInput(values=(), records=None)
    _, first_text = pairs[0]
    if _looks_like_envelope(first_text):
        records = tuple(parse_envelope_line(lineno, text) for lineno, text in pairs)
        if accept_kinds is not None:
            _check_kinds(records, accept_kinds)
        return StdinInput(values=(), records=records)
    # Bare mode: guard against a later envelope line being silently treated as
    # a bare identifier (first-line detection alone would miss it).
    values: list[str] = []
    for lineno, text in pairs:
        if _looks_like_envelope(text):
            raise ConfigError(
                f"mixed bare/envelope input on stdin (line {lineno})", category="invalid"
            )
        values.append(text)
    return StdinInput(values=tuple(values), records=None)


def _check_kinds(records: Sequence[PipeEnvelope], accept_kinds: Collection[str]) -> None:
    accepted = sorted(accept_kinds)
    for env in records:
        if env.kind is not None and env.kind not in accept_kinds:
            expected = ", ".join(q(kind) for kind in accepted) or "bare identifiers"
            raise UsageError(
                f"line {env.lineno}: record kind {q(env.kind)} is not accepted here; "
                f"expected {expected}"
            )


def _looks_like_envelope(text: str) -> bool:
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return False
    return is_envelope_line(obj)
