"""CLI helpers shared by every Cyclopts command in the suite."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Annotated, Any, Literal, NoReturn

from cyclopts import App, Parameter
from cyclopts.exceptions import CycloptsError
from pydantic import BaseModel
from rich.console import Console

from untaped.errors import HttpError, UntapedError
from untaped.output import OutputFormat
from untaped.ui import UiContext, ui_context
from untaped.verbose import is_verbose

FormatOption = Annotated[
    OutputFormat,
    Parameter(name=["--format", "-f"], help="Output format."),
]
"""Shared ``--format / -f`` option for any command that prints rows."""

ColumnsOption = Annotated[
    list[str] | None,
    Parameter(
        name=["--columns", "-c"],
        help="Columns to include (repeatable).",
        consume_multiple=False,
    ),
]
"""Shared ``--columns / -c`` option for any command that prints rows."""


def create_app(*, name: str, help: str = "") -> App:
    """Create a Cyclopts app with the suite's default command-group settings."""
    return App(name=name, help=help)


def echo(message: object = "", *, err: bool = False, nl: bool = True) -> None:
    """Print a CLI message to stdout or stderr."""
    end = "\n" if nl else ""
    print(message, file=sys.stderr if err else sys.stdout, end=end)


def raise_usage(message: str) -> NoReturn:
    """Raise a command-usage error with the suite's stable exit code."""
    echo(f"error: {message}", err=True)
    raise SystemExit(2)


def render_rows(
    rows: Sequence[dict[str, object]],
    *,
    fmt: OutputFormat,
    columns: list[str] | None = None,
    empty: str | bool | None = None,
    kind: str | None = None,
) -> str:
    """Render a row collection: themed table for humans, plain output for pipes.

    Only ``table`` goes through the settings-resolved :func:`ui_context` —
    structured formats (json, raw, pipe, ...) must stay byte-stable regardless of
    the active theme, so they render through a bare :class:`UiContext`. ``empty``
    is a human hint printed to stderr only when ``table`` output has no rows.
    ``kind`` tags ``--format pipe`` records with a producer hint (ignored by
    every other format).
    """
    if columns == ["?"]:
        _print_available_columns(list(rows[0]) if rows else [])
        return ""
    ui = ui_context() if fmt == "table" else UiContext()
    return ui.collection(rows, fmt=fmt, columns=columns, empty=empty, kind=kind)


def _print_available_columns(keys: Iterable[str]) -> None:
    """Print the addressable top-level column names to stderr (for ``--columns ?``)."""
    names = list(dict.fromkeys(keys))
    if not names:
        echo("no columns available (no records to inspect)", err=True)
        return
    echo("available columns:", err=True)
    for name in names:
        echo(f"  {name}", err=True)


def emit(
    records: BaseModel | Mapping[str, object] | Sequence[BaseModel | Mapping[str, object]],
    *,
    fmt: OutputFormat,
    columns: list[str] | None = None,
    empty: str | bool | None = None,
    kind: str | None = None,
) -> None:
    """Render records to stdout, dispatching by shape.

    A single model or mapping renders as a vertical ``key: value`` detail view
    (a bare object under structured formats); a sequence renders as a collection
    (themed table for humans, array/NDJSON for pipes). Accepts pydantic models
    directly — no manual ``model_dump()`` — and writes the result itself, so
    there is no "forgot to ``echo``" silent-no-output trap. ``empty`` and
    ``kind`` behave as in :func:`render_rows`; ``empty`` applies to a sequence
    only.
    """
    if columns == ["?"]:
        _print_available_columns(_candidate_columns(records))
        return
    if isinstance(records, BaseModel | Mapping):
        ui = ui_context() if fmt == "table" else UiContext()
        rendered = ui.detail(_as_row(records), fmt=fmt, columns=columns, kind=kind)
    else:
        # The collection path is exactly render_rows; reuse it (it returns the
        # string and emits any empty-state hint to stderr itself).
        rendered = render_rows(
            [_as_row(record) for record in records],
            fmt=fmt,
            columns=columns,
            empty=empty,
            kind=kind,
        )
    if rendered:
        echo(rendered)


def _as_row(record: BaseModel | Mapping[str, object]) -> dict[str, object]:
    """Normalize a model or mapping into a plain row dict."""
    if isinstance(record, BaseModel):
        return record.model_dump()
    return dict(record)


def _candidate_columns(
    records: BaseModel | Mapping[str, object] | Sequence[BaseModel | Mapping[str, object]],
) -> list[str]:
    """Top-level column names a record exposes (for ``emit(..., columns=['?'])``)."""
    if isinstance(records, BaseModel | Mapping):
        return list(_as_row(records))
    for record in records:
        return list(_as_row(record))
    return []


def run_cyclopts_app(
    app: App,
    tokens: Iterable[str] | None,
    *,
    console: Console | None = None,
    error_console: Console | None = None,
    result_action: Literal[
        "return_value",
        "call_if_callable",
        "print_non_int_return_int_as_exit_code",
        "print_str_return_int_as_exit_code",
        "print_str_return_zero",
        "print_non_none_return_int_as_exit_code",
        "print_non_none_return_zero",
        "return_int_as_exit_code_else_zero",
        "print_non_int_sys_exit",
        "sys_exit",
        "return_none",
        "return_zero",
        "print_return_zero",
        "sys_exit_zero",
        "print_sys_exit_zero",
    ]
    | Callable[[Any], Any]
    | None = None,
) -> object:
    """Run a Cyclopts app while preserving untaped's usage-error contract.

    Also converts a broken downstream pipe — the consumer closed it early, e.g.
    ``untaped-tool list | head`` or a consumer that exits before reading all of
    its input — into a clean ``SystemExit(1)``. Without this the producer's
    buffered stdout flush fails at interpreter shutdown and Python prints a
    noisy ``Exception ignored while flushing sys.stdout: BrokenPipeError``.
    """
    try:
        result = app(
            tokens,
            console=console,
            error_console=error_console,
            exit_on_error=False,
            print_error=False,
            result_action=result_action,
        )
    except CycloptsError as exc:
        echo(f"error: {exc}", err=True)
        raise SystemExit(2) from exc
    except BrokenPipeError:
        # Pipe broke mid-write (output large enough to flush before we got here).
        _exit_broken_pipe()
    except SystemExit:
        # cyclopts exits (0 on success) rather than returning. Flush buffered
        # stdout now so a broken pipe surfaces here — catchable — instead of at
        # interpreter shutdown, where it can't be handled.
        _flush_stdout()
        raise
    _flush_stdout()
    return result


def _flush_stdout() -> None:
    """Flush stdout, converting a broken pipe into a clean exit."""
    try:
        sys.stdout.flush()
    except BrokenPipeError:
        _exit_broken_pipe()


def _exit_broken_pipe() -> NoReturn:
    """Silence the interpreter's final stdout flush, then exit 1.

    Redirecting the stdout fd to ``/dev/null`` stops Python re-raising the
    broken pipe when it flushes the standard streams on the way out. The guard
    covers streams with no real fd (a captured ``StringIO`` under tests)."""
    with suppress(OSError, ValueError):
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    raise SystemExit(1) from None


def existing_directory(type_: object, value: Path | None) -> None:
    """Cyclopts validator for an existing directory path."""
    if value is None:
        return
    if not value.exists():
        raise ValueError(f"path does not exist: {value}")
    if not value.is_dir():
        raise ValueError(f"path is not a directory: {value}")


def existing_file(type_: object, value: Path | None) -> None:
    """Cyclopts validator for an existing file path."""
    if value is None:
        return
    if not value.exists():
        raise ValueError(f"path does not exist: {value}")
    if not value.is_file():
        raise ValueError(f"path is not a file: {value}")


def parse_kv_pairs(values: Iterable[str] | None, *, flag: str) -> dict[str, str]:
    """Parse repeated ``KEY=VALUE`` flag entries into a dict.

    Splits on the first ``=`` so values containing ``=`` survive intact.
    Malformed entries are rejected up front rather than passed through.
    """
    if not values:
        return {}
    out: dict[str, str] = {}
    for entry in values:
        key, sep, value = entry.partition("=")
        key = key.strip()
        if not sep or not key:
            raise_usage(f"{flag} expects KEY=VALUE (got {entry!r})")
        out[key] = value
    return out


def resolve_each[R](ids: list[str], fn: Callable[[str], R]) -> tuple[list[R], bool]:
    """Resolve each identifier via ``fn``; aggregate per-id failures.

    Echoes ``error: <id>: <exc>`` to stderr for any :class:`UntapedError` and
    returns ``(results, any_failed)`` so the caller decides exit code and
    aggregate rendering. Companion to :func:`read_identifiers` for stdin-fed
    list commands across domains.

    Only :class:`UntapedError` is caught: non-:class:`UntapedError` exceptions
    (including :class:`SystemExit` raised by interactive prompts) propagate
    immediately, aborting the loop. This is intentional — bugs and explicit
    user aborts must not be swallowed alongside per-id resolution failures.
    """
    results: list[R] = []
    any_failed = False
    for id_ in ids:
        try:
            results.append(fn(id_))
        except UntapedError as exc:
            echo(f"error: {id_}: {_format_error(exc)}", err=True)
            any_failed = True
    return results, any_failed


def clamp_parallel(requested: int, *, cap: int, policy: str) -> int:
    """Cap ``--parallel`` at ``cap`` with a uniform stderr warning.

    Shared by every Cyclopts command that exposes ``-j / --parallel``
    (workspace sync, workspace foreach, awx apply, ...) so the
    cap-with-warning shape is one helper, not one per call site.
    Friendly clamp rather than ``BadParameter`` so shell idioms like
    ``-j $(nproc)`` keep composing on hosts where ``nproc`` already
    exceeds the cap.

    Only handles the upper bound. ``< 1`` policy stays per-caller
    (workspace foreach silently coerces; sync and awx apply reject) — the lower
    bound isn't a typo-vs-typo judgement, it's per-command UX.

    ``policy`` is a short human-readable rationale (e.g.
    ``"2 * os.cpu_count()"`` or ``"HTTP connection pool default"``)
    appended in parens so users know *why* their value was capped
    without grepping source.
    """
    if requested <= cap:
        return requested
    echo(
        f"warning: --parallel {requested} clamped to {cap} ({policy})",
        err=True,
    )
    return cap


@contextmanager
def report_errors() -> Iterator[None]:
    """Convert :class:`UntapedError` into a clean stderr message + exit code 1.

    Wrap every Cyclopts command body in this so users see ``error: ...``
    instead of a Python traceback. Non-:class:`UntapedError` exceptions are
    left to Cyclopts' default handling — those represent bugs we want to see.
    """
    try:
        yield
    except UntapedError as exc:
        echo(f"error: {_format_error(exc)}", err=True)
        raise SystemExit(1) from exc


def _format_error(exc: UntapedError) -> str:
    message = str(exc)
    if not isinstance(exc, HttpError) or not exc.body:
        return message
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
