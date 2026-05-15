"""Shared per-identifier resolution helpers for CLI commands.

`get_one` dispatches an identifier between numeric-id and name lookup;
`resolve_each` walks a list of identifiers and aggregates per-id
``UntapedError`` rows to stderr with an ``any_failed`` flag for the
caller's exit code. Used by ``get``, ``list --stdin``, and the
spec-driven membership subcommands so identifier-handling stays in
one place.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import typer
from untaped_core import UntapedError

from untaped_awx.application import GetResource
from untaped_awx.infrastructure.spec import AwxResourceSpec


def get_one(
    getter: GetResource,
    spec: AwxResourceSpec,
    identifier: str,
    scope: dict[str, str] | None,
    *,
    by_name: bool = False,
) -> dict[str, Any]:
    # `isdecimal()` matches Unicode category Nd — exactly the set
    # `int()` accepts. `isdigit()` admits superscripts/subscripts
    # like "²" that `int()` would reject with ValueError.
    if not by_name and identifier.isdecimal():
        return getter(spec, id_=int(identifier))
    return getter(spec, name=identifier, scope=scope)


def resolve_each[R](ids: list[str], fn: Callable[[str], R]) -> tuple[list[R], bool]:
    """Resolve each ``id`` via ``fn``; echo ``error: <id>: <exc>`` for
    per-id ``UntapedError``s. Returns ``(results, any_failed)`` so the
    caller can decide its exit code and any aggregate rendering.
    """
    results: list[R] = []
    any_failed = False
    for n in ids:
        try:
            results.append(fn(n))
        except UntapedError as exc:
            typer.echo(f"error: {n}: {exc}", err=True)
            any_failed = True
    return results, any_failed
