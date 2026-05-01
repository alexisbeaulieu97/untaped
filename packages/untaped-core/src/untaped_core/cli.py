"""CLI helpers shared by every Typer command in the suite."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import typer

from untaped_core.errors import UntapedError


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
