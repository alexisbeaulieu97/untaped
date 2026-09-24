"""Stdin helpers for piping values into commands."""

from __future__ import annotations

import json
import sys
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from pathlib import Path

from untaped.errors import ConfigError, UsageError
from untaped.messages import q
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


def resolve_text_input(*, value: str | None, file: Path | None, what: str = "body") -> str:
    """Resolve one text input from flag value > file > piped stdin.

    ``value`` and ``file`` together are refused; by convention ``what`` is
    also the flag stem (``--<what>`` / ``--<what>-file``). A file read trims
    its terminal newline like :func:`read_stdin_text`. Empty resolved text
    raises :class:`ConfigError` naming ``what``.
    """
    if value is not None and file is not None:
        raise UsageError(f"provide --{what} or --{what}-file, not both")
    if value is not None:
        return _require_text(value, what=what)
    if file is not None:
        try:
            with open(file, encoding="utf-8", newline="") as handle:
                return _require_text(_trim_terminal_newline(handle.read()), what=what)
        except OSError as exc:
            raise ConfigError(f"could not read {file}: {exc}") from exc
    text = read_stdin_text()
    return _require_text(text, what=what)


def _require_text(text: str, *, what: str) -> str:
    if not text.strip():
        raise ConfigError(f"no {what} provided (use --{what}, --{what}-file, or pipe it on stdin)")
    return text


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
    *, accept_kinds: Collection[str] | None = None, what: str = "identifiers"
) -> StdinInput:
    """Read stdin as bare values or a pipe stream, detected from the first line.

    The one core stdin reader for commands that accept either shape (a
    name/ID list or another command's ``--format pipe`` output). Raises
    :class:`ConfigError` on empty stdin (``no <what> received on stdin``), on
    mixed bare/envelope input, and on malformed envelopes (line-precise).
    ``accept_kinds`` declares the record kinds the command understands: an
    envelope whose ``kind`` is set and not listed is a :class:`UsageError`
    (exit 2), so ``awx hosts list -f pipe | awx jobs get --stdin`` can never
    treat host IDs as job IDs. Records without a kind are accepted.
    """
    pairs = _read_raw_lines()
    if not pairs:
        raise ConfigError(f"no {what} received on stdin")
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
            raise ConfigError(f"mixed bare/envelope input on stdin (line {lineno})")
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


def read_records(*, accept_kinds: Collection[str] | None = None) -> list[PipeEnvelope]:
    """Read an untaped ``--format pipe`` stream from stdin into envelopes.

    The dual of :func:`read_identifiers` for consumers that want the full
    record, not just an identifier. There is no alternate source, so (unlike
    :func:`read_identifiers`) it takes no flag. Raises :class:`ConfigError` on an
    empty stream or any malformed line (line-precise), and :class:`UsageError`
    for a kind outside ``accept_kinds`` (see :func:`read_stdin_input`).
    """
    pairs = _read_raw_lines()
    if not pairs:
        raise ConfigError("no records received on stdin")
    records = [parse_envelope_line(lineno, text) for lineno, text in pairs]
    if accept_kinds is not None:
        _check_kinds(records, accept_kinds)
    return records


def read_identifiers(
    positional: list[str],
    *,
    stdin: bool,
    id_field: str | None = None,
    accept_kinds: Collection[str] | None = None,
) -> list[str]:
    """Resolve identifiers from positional args or stdin (exactly one).

    Used by every CLI command that takes a list of names/IDs to honour
    the documented pipeline shape (``list --format raw | get --stdin``).
    Mixing positional + ``--stdin`` is a :class:`UsageError`: a misplaced flag
    would silently act on the wrong set. No identifiers at all is also a
    usage error, and empty stdin a :class:`ConfigError`, so commands don't
    no-op when given nothing to do.

    On stdin the input may be either bare newline-separated identifiers or an
    untaped ``--format pipe`` stream (see :func:`read_stdin_input`). In
    envelope mode each record's ``id_field`` is extracted, so a producer's rich
    output pipes straight into an identifier consumer; ``accept_kinds``
    rejects records of any other kind with exit 2.
    """
    if stdin and positional:
        raise UsageError("provide identifiers as positional args or via --stdin, not both")
    if stdin:
        piped = read_stdin_input(accept_kinds=accept_kinds)
        if piped.records is None:
            return list(piped.values)
        if id_field is None:
            raise ConfigError(
                "stdin is untaped pipe format but this command cannot map records to identifiers"
            )
        return [_extract_id(env, id_field) for env in piped.records]
    if not positional:
        raise UsageError("at least one identifier is required (or use --stdin)")
    return positional


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
