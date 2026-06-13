"""CLI helpers shared by every Cyclopts command in the suite."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Any, Literal, NoReturn

from cyclopts import App, Parameter
from cyclopts.exceptions import CycloptsError
from rich.console import Console

from untaped.errors import HttpError, UntapedError
from untaped.output import OutputFormat
from untaped.settings import get_settings
from untaped.ui import UiContext, ui_context

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

ProfileOverrideOption = Annotated[
    str | None,
    Parameter(
        name="--profile",
        help="Override the active profile for this command only.",
    ),
]
"""Deprecated (plugin API v4): command-local read-time profile override.

Profile selection is plugin-owned now — new code should rely on the root
``--profile`` option contributed by the untaped-profile plugin. This alias
stays importable because released v3-era plugins annotate their command
parameters with it; removal is gated on the plugin-API-v4 rollout finishing
across the plugin repos.
"""


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
) -> str:
    """Render a row collection: themed table for humans, plain output for pipes.

    Only ``table`` goes through the settings-resolved :func:`ui_context` —
    structured formats (json, raw, ...) must stay byte-stable regardless of the
    active theme, so they render through a bare :class:`UiContext`.
    """
    ui = ui_context() if fmt == "table" else UiContext()
    return ui.collection(rows, fmt=fmt, columns=columns)


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
    """Run a Cyclopts app while preserving untaped's usage-error contract."""
    try:
        return app(
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


@contextmanager
def profile_override(name: str | None) -> Iterator[None]:
    """Temporarily override ``UNTAPED_PROFILE`` for a command body.

    Deprecated (plugin API v4): profile selection is plugin-owned and happens
    before dispatch via the root ``--profile`` option. This shim stays because
    released v3-era plugins wrap command bodies in it; it composes with the
    untaped-profile plugin's scoped settings layout (which honours
    ``UNTAPED_PROFILE``) and is inert under the default flat layout. Removal
    is gated on the plugin-API-v4 rollout finishing across the plugin repos.
    """
    if name is None:
        yield
        return

    previous = os.environ.get("UNTAPED_PROFILE")
    had_previous = "UNTAPED_PROFILE" in os.environ
    os.environ["UNTAPED_PROFILE"] = name
    get_settings.cache_clear()
    try:
        yield
    finally:
        if had_previous and previous is not None:
            os.environ["UNTAPED_PROFILE"] = previous
        else:
            os.environ.pop("UNTAPED_PROFILE", None)
        get_settings.cache_clear()


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
    if isinstance(exc, HttpError) and exc.body:
        return f"{message}\nresponse: {exc.body}"
    return message
