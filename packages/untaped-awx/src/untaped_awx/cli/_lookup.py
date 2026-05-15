"""Per-identifier resolution loop shared by CLI commands.

``resolve_each`` walks a list of identifiers, calls ``fn(id)``, and
echoes ``error: <id>: <exc>`` for any ``UntapedError`` so the caller
gets ``(results, any_failed)`` for stable per-id error reporting +
exit-code aggregation. Used by ``get``, ``list --stdin``, and the
spec-driven membership subcommands. Identifier-to-resource dispatch
(digits → id, otherwise name) lives on
:meth:`untaped_awx.application.GetResource.by_identifier` so application
callers (e.g. :class:`ListWorkflowNodes`) can reuse the same rule.
"""

from __future__ import annotations

from collections.abc import Callable

import typer
from untaped_core import UntapedError


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
