"""Failure diagnostics: how errors render, JSON Lines on stderr, and the run's exit code.

Four things live here, shared by every stderr writer in the shell:

- :func:`attribute` is the one classifier of any exception: its category
  and system (an :class:`~untaped.errors.UntapedError` says its own; Ctrl-C
  is ``interrupted``; anything else is a ``failed`` error in ``untaped``).
  :class:`ErrorInfo` is that attribution plus the message and hint, the
  ``error`` of a failed row and the body of every JSON error line.
- :func:`format_error` renders an error the way ``report_errors`` prints it
  (HTTP URL and API message, the ``hint:`` line), with URL passwords masked.
- **JSON diagnostics.** With ``--format json|yaml|pipe`` (recorded by the
  root's format resolution through :func:`note_output_format`) or
  ``UNTAPED_DIAGNOSTICS=json``, stderr carries one JSON object per line
  instead of text: errors (with ``category``, ``system``, ``retryable``,
  ``hint``, ``exit_code`` and ``details``), per-item errors (plus ``item``),
  warnings, hints and other notes. ``UNTAPED_DIAGNOSTICS=text`` forces text.
  stdout never changes.
- **The run's exit code.** Every failure reported or turned into a row is
  noted (:func:`note_failure`), so the process exits with the most severe
  category seen anywhere in the run (:func:`failure_exit_code`, precedence
  ``130 > 2 > 4 > 5 > 1 > 3 > 0``).

Everything is scoped to one invocation by :func:`diagnostics_scope`.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, TextIO, overload

from pydantic import BaseModel, ConfigDict

from untaped.errors import (
    ErrorCategory,
    ExitCode,
    HttpError,
    UntapedError,
    combine_exit_codes,
)
from untaped.redaction import redact_url_password
from untaped.verbose import is_verbose

#: Environment variable choosing the stderr format: ``json`` or ``text``.
DIAGNOSTICS_ENV = "UNTAPED_DIAGNOSTICS"
#: Output formats whose stderr diagnostics default to JSON Lines.
STRUCTURED_FORMATS = frozenset({"json", "yaml", "pipe"})

_HINT_PREFIX = "hint: "
_LINE_LEVELS = (("error: ", "error"), ("warning: ", "warning"), (_HINT_PREFIX, "hint"))


class _Run:
    """One invocation: its chosen output format and its most severe failure."""

    def __init__(self) -> None:
        self.output_format: str | None = None
        self._lock = threading.Lock()
        self._worst: int = ExitCode.OK

    def note(self, code: int) -> None:
        with self._lock:
            self._worst = combine_exit_codes(self._worst, code)

    @property
    def worst(self) -> int:
        with self._lock:
            return self._worst


_run: ContextVar[_Run | None] = ContextVar("untaped_diagnostics_run", default=None)


@contextmanager
def diagnostics_scope() -> Iterator[None]:
    """Scope one invocation: no output format recorded yet, no failure seen."""
    token = _run.set(_Run())
    try:
        yield
    finally:
        _run.reset(token)


def note_output_format(fmt: str | None) -> None:
    """Record the invocation's chosen ``--format`` (selects JSON diagnostics).

    Outside a :func:`diagnostics_scope` there is no invocation to record it
    for, and nothing happens.
    """
    run = _run.get()
    if run is not None:
        run.output_format = fmt


def json_diagnostics() -> bool:
    """Whether stderr diagnostics are JSON Lines for this invocation.

    ``UNTAPED_DIAGNOSTICS=json`` or ``text`` decides; any other value (or
    none) follows the output format: ``json``, ``yaml`` and ``pipe`` get JSON.
    """
    choice = os.environ.get(DIAGNOSTICS_ENV, "").strip().lower()
    if choice in ("json", "text"):
        return choice == "json"
    run = _run.get()
    return run is not None and run.output_format in STRUCTURED_FORMATS


def attribute(error: BaseException) -> tuple[ErrorCategory, str]:
    """``(category, system)`` of any exception: the one place that classifies them."""
    if isinstance(error, UntapedError):
        return error.category, error.system
    if isinstance(error, KeyboardInterrupt):
        return ErrorCategory.INTERRUPTED, "untaped"
    return ErrorCategory.FAILED, "untaped"


class ErrorInfo(BaseModel):
    """Why something failed: the ``error`` of a failed row, the body of a JSON error line.

    The machine-readable twin of a human ``detail``: the failure's
    ``category`` (which selects the exit code), the ``system`` responsible,
    whether a retry can help, the message and an optional hint (without its
    ``hint:`` prefix). :meth:`from_exception` builds one without side
    effects; :func:`note_failure` builds one and counts it toward the run's
    exit code.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    category: ErrorCategory
    system: str
    retryable: bool
    message: str
    hint: str | None = None

    @classmethod
    def from_exception(cls, error: BaseException, *, message: str | None = None) -> ErrorInfo:
        """The attribution of ``error``, rendered as :func:`format_error` does.

        ``message`` replaces the rendered message (e.g. a redacted one); a
        ``hint:`` line in it becomes ``hint``.
        """
        text, hint = error_message(error, message=message)
        category, system = attribute(error)
        return cls(
            category=category,
            system=system,
            retryable=category.retryable,
            message=text,
            hint=hint,
        )


@overload
def note_failure(
    failure: BaseException | ErrorInfo, *, message: str | None = None
) -> ErrorInfo: ...
@overload
def note_failure(failure: ErrorCategory) -> None: ...
def note_failure(
    failure: BaseException | ErrorInfo | ErrorCategory, *, message: str | None = None
) -> ErrorInfo | None:
    """Count a failure toward the invocation's exit code (see :func:`failure_exit_code`).

    Given an exception (or an :class:`ErrorInfo`), returns its
    :class:`ErrorInfo` (``message`` as in :meth:`ErrorInfo.from_exception`),
    so a failed row reads ``error=note_failure(exc)``.
    """
    if isinstance(failure, ErrorCategory):
        _note(failure.exit_code)
        return None
    info = (
        failure
        if isinstance(failure, ErrorInfo)
        else ErrorInfo.from_exception(failure, message=message)
    )
    _note(info.category.exit_code)
    return info


def _note(code: int) -> None:
    run = _run.get()
    if run is not None:
        run.note(code)


def failure_exit_code(*codes: int) -> int:
    """The exit code of a failed run: the most severe of ``codes`` and every noted failure.

    Never below ``1``: a failed run whose failures carry no category exits ``1``.
    """
    run = _run.get()
    code = combine_exit_codes(*codes, run.worst if run is not None else ExitCode.OK)
    return code if code not in (ExitCode.OK, ExitCode.PREDICATE) else ExitCode.FAILURE


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def format_error(exc: UntapedError) -> str:
    """Render an :class:`UntapedError` the way ``report_errors`` prints it.

    Adds the URL to bodiless HTTP errors and surfaces the API's own message
    from a JSON error body (the raw body under ``--verbose``); an error whose
    message already describes its body (``describes_body``) adds the raw body
    under ``--verbose`` only. A ``hint`` not already in the message follows
    on its own ``hint:`` line. URL passwords are masked.
    """
    message = _with_body(exc)
    if exc.hint and exc.hint not in message:
        message = f"{message}\n{_HINT_PREFIX}{exc.hint}"
    return redact_url_password(message)


def _with_body(exc: UntapedError) -> str:
    message = str(exc)
    if not isinstance(exc, HttpError):
        return message
    if not exc.body:
        if exc.url and exc.url not in message:
            message = f"{message} for {exc.url}"
        return message
    if exc.describes_body:
        return f"{message}\nresponse: {exc.body}" if is_verbose() else message
    friendly = _api_error_message(exc.body)
    if friendly is None:
        # Unparseable / unrecognised body — show it raw so detail isn't lost.
        return f"{message}\nresponse: {exc.body}"
    if is_verbose():
        return f"{message} — {friendly}\nresponse: {exc.body}"
    return f"{message} — {friendly}"


def _api_error_message(body: str) -> str | None:
    """Pull a human message out of a JSON error body, if present.

    Recognises the shapes most JSON APIs use — a top-level
    ``message``/``error``/``detail`` string or ``errors: [{"message": ...}]``
    (GitHub, AWX, DRF, ...) — and returns the first match. Returns ``None`` for
    a non-JSON body or an unrecognised shape so the caller falls back to the raw
    snippet.
    """
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    for key in ("message", "error", "detail"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    errors = data.get("errors")
    if isinstance(errors, list):
        for item in errors:
            if isinstance(item, dict):
                nested = item.get("message")
                if isinstance(nested, str) and nested.strip():
                    return nested.strip()
    return None


def split_hint(text: str) -> tuple[str, str | None]:
    """Split ``hint: …`` lines off ``text``: ``(message, hint)`` (hint without its prefix)."""
    kept: list[str] = []
    hints: list[str] = []
    for line in text.split("\n"):
        if line.startswith(_HINT_PREFIX):
            hints.append(line.removeprefix(_HINT_PREFIX))
        else:
            kept.append(line)
    return "\n".join(kept).strip(), "\n".join(hints) or None


def error_message(error: BaseException, *, message: str | None = None) -> tuple[str, str | None]:
    """``(message, hint)`` for ``error``: its rendering, or ``message``, with hints split off."""
    if message is None:
        message = format_error(error) if isinstance(error, UntapedError) else _plain(error)
    text, hint = split_hint(redact_url_password(message))
    if hint is None and isinstance(error, UntapedError):
        hint = error.hint
    return text, hint


def _plain(error: BaseException) -> str:
    return str(error) or type(error).__name__


def error_record(
    error: BaseException | ErrorInfo, *, item: str | None = None, message: str | None = None
) -> dict[str, Any]:
    """The JSON diagnostic for one failure (``item`` names a per-item failure).

    Its :class:`ErrorInfo` fields plus ``level``, ``item``, ``exit_code`` and
    the error's ``details`` (none for an :class:`ErrorInfo`).
    """
    info = (
        error if isinstance(error, ErrorInfo) else ErrorInfo.from_exception(error, message=message)
    )
    record: dict[str, Any] = {"level": "error"}
    if item is not None:
        record["item"] = item
    details = error.details if isinstance(error, UntapedError) else {}
    return (
        record
        | info.model_dump(mode="json")
        | {
            "exit_code": int(info.category.exit_code),
            "details": dict(details),
        }
    )


def line_record(text: str) -> dict[str, Any] | None:
    """The JSON diagnostic for one text stderr message (``None`` for a blank one).

    ``error:``, ``warning:`` and ``hint:`` prefixes select the level (an
    ``error:`` message keeps its ``hint:`` lines as ``hint``); anything
    else is ``info``. URL passwords are masked.
    """
    if not text.strip():
        return None
    text = redact_url_password(text)
    for prefix, level in _LINE_LEVELS:
        if text.startswith(prefix):
            body = text.removeprefix(prefix)
            if level != "error":
                return {"level": level, "message": body.strip()}
            message, hint = split_hint(body)
            return {"level": level, "message": message, "hint": hint}
    return {"level": "info", "message": text.strip()}


def render_record(record: Mapping[str, Any]) -> str:
    """One JSON diagnostic as its line of text."""
    return json.dumps(record, default=str)


def write_record(record: Mapping[str, Any], stream: TextIO | None = None) -> None:
    """Write one JSON diagnostic line to ``stream`` (stderr by default)."""
    print(render_record(record), file=stream or sys.stderr, flush=True)


__all__ = [
    "DIAGNOSTICS_ENV",
    "STRUCTURED_FORMATS",
    "ErrorInfo",
    "attribute",
    "diagnostics_scope",
    "error_message",
    "error_record",
    "failure_exit_code",
    "format_error",
    "json_diagnostics",
    "line_record",
    "note_failure",
    "note_output_format",
    "render_record",
    "split_hint",
    "write_record",
]
