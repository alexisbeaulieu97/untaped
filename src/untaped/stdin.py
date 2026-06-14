"""Stdin helpers for piping values into commands."""

from __future__ import annotations

import json
import sys

from untaped.errors import ConfigError
from untaped.pipe import PipeEnvelope, is_envelope_line, parse_envelope_line


def read_stdin() -> list[str]:
    """Read newline-separated values from stdin.

    Returns an empty list if stdin is a tty (interactive) — never blocks
    waiting for user input. Empty lines are skipped; surrounding whitespace
    on each line is stripped.
    """
    if sys.stdin.isatty():
        return []
    return [stripped for line in sys.stdin if (stripped := line.strip())]


def _read_raw_lines() -> list[tuple[int, str]]:
    """Read stdin as ``(1-based line number, stripped line)`` pairs.

    Returns an empty list when stdin is a tty (never blocks). Blank lines are
    skipped but the line numbers track the original physical line, so envelope
    parse errors point at the right place.
    """
    if sys.stdin.isatty():
        return []
    pairs: list[tuple[int, str]] = []
    for lineno, line in enumerate(sys.stdin, start=1):
        stripped = line.strip()
        if stripped:
            pairs.append((lineno, stripped))
    return pairs


def read_records() -> list[PipeEnvelope]:
    """Read an untaped ``--format pipe`` stream from stdin into envelopes.

    The dual of :func:`read_identifiers` for consumers that want the full
    record, not just an identifier. There is no alternate source, so (unlike
    :func:`read_identifiers`) it takes no flag. Raises :class:`ConfigError` on an
    empty stream or any malformed line (line-precise).
    """
    pairs = _read_raw_lines()
    if not pairs:
        raise ConfigError("no records received on stdin")
    return [parse_envelope_line(lineno, text) for lineno, text in pairs]


def read_identifiers(
    positional: list[str], *, stdin: bool, id_field: str | None = None
) -> list[str]:
    """Resolve identifiers from positional args or stdin (exactly one).

    Used by every CLI command that takes a list of names/IDs to honour
    the documented pipeline shape (``list --format raw | get --stdin``).
    Mixing positional + ``--stdin`` is refused: a misplaced flag would
    silently act on the wrong set. Empty input on either side is also
    refused so commands don't no-op when given nothing to do.

    On stdin the input may be either bare newline-separated identifiers or an
    untaped ``--format pipe`` stream. The format is detected from the first
    non-blank line; in envelope mode each record's ``id_field`` is extracted, so
    a producer's rich output pipes straight into an identifier consumer.
    """
    if stdin and positional:
        raise ConfigError("provide identifiers as positional args or via --stdin, not both")
    if stdin:
        pairs = _read_raw_lines()
        if not pairs:
            raise ConfigError("no identifiers received on stdin")
        return _identifiers_from_stdin(pairs, id_field=id_field)
    if not positional:
        raise ConfigError("at least one identifier is required (or use --stdin)")
    return positional


def _identifiers_from_stdin(pairs: list[tuple[int, str]], *, id_field: str | None) -> list[str]:
    _, first_text = pairs[0]
    if _looks_like_envelope(first_text):
        if id_field is None:
            raise ConfigError(
                "stdin is untaped pipe format but this command cannot map records to identifiers"
            )
        return [_extract_id(parse_envelope_line(lineno, text), id_field) for lineno, text in pairs]
    # Bare mode — guard against a later envelope line being silently treated as
    # a bare identifier (first-line detection alone would miss it).
    ids: list[str] = []
    for lineno, text in pairs:
        if _looks_like_envelope(text):
            raise ConfigError(f"mixed bare/envelope input on stdin (line {lineno})")
        ids.append(text)
    return ids


def _extract_id(env: PipeEnvelope, id_field: str) -> str:
    value = env.record.get(id_field)
    if value is None:
        raise ConfigError(f"line {env.lineno}: record {id_field!r} is missing or null")
    identifier = str(value).strip()
    if not identifier:
        raise ConfigError(f"line {env.lineno}: record {id_field!r} is blank")
    return identifier


def _looks_like_envelope(text: str) -> bool:
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return False
    return is_envelope_line(obj)
