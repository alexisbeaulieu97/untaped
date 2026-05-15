"""CLI helpers shared by every Typer command in the suite."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from typing import Annotated

import typer

from untaped_core.errors import UntapedError
from untaped_core.output import OutputFormat

FormatOption = Annotated[
    OutputFormat,
    typer.Option("--format", "-f", help="Output format."),
]
"""Shared ``--format / -f`` option for any command that prints rows."""

ColumnsOption = Annotated[
    list[str] | None,
    typer.Option("--columns", "-c", help="Columns to include (repeatable)."),
]
"""Shared ``--columns / -c`` option for any command that prints rows."""


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
            raise typer.BadParameter(f"{flag} expects KEY=VALUE (got {entry!r})", param_hint=flag)
        out[key] = value
    return out


def resolve_each[R](ids: list[str], fn: Callable[[str], R]) -> tuple[list[R], bool]:
    """Resolve each identifier via ``fn``; aggregate per-id failures.

    Echoes ``error: <id>: <exc>`` to stderr for any :class:`UntapedError` and
    returns ``(results, any_failed)`` so the caller decides exit code and
    aggregate rendering. Companion to :func:`read_identifiers` for stdin-fed
    list commands across domains.
    """
    results: list[R] = []
    any_failed = False
    for id_ in ids:
        try:
            results.append(fn(id_))
        except UntapedError as exc:
            typer.echo(f"error: {id_}: {exc}", err=True)
            any_failed = True
    return results, any_failed


@contextmanager
def report_errors() -> Iterator[None]:
    """Convert :class:`UntapedError` into a clean stderr message + exit code 1.

    Wrap every Typer command body in this so users see ``error: ...`` instead
    of a Python traceback. Non-:class:`UntapedError` exceptions are left to
    Typer/Click's default handling — those represent bugs we want to see.
    """
    try:
        yield
    except UntapedError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
