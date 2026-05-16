"""Shared apply runner used by top-level ``awx apply`` and per-resource
``awx <kind> apply``.

Composition root: imports the YAML reader from infrastructure, wraps it
with optional kind-filter logic, and hands the result to the
application-layer :class:`ApplyFile` use case (which only sees a
``ResourceDocumentReader`` Protocol).
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import typer
from untaped_core import OutputFormat, format_output

from untaped_awx.application import ApplyFile, ApplyResource
from untaped_awx.application.apply_file import APPLY_PARALLEL_CAP
from untaped_awx.application.ports import ResourceDocumentReader
from untaped_awx.cli._context import AwxContext
from untaped_awx.cli.format import diff_lines, outcome_rows
from untaped_awx.domain import Resource
from untaped_awx.infrastructure.yaml_io import read_resources


def run_apply(
    ctx: AwxContext,
    file: Path,
    *,
    write: bool,
    fail_fast: bool,
    fmt: OutputFormat = "table",
    columns: list[str] | None = None,
    kind_filter: str | None = None,
    cli_name: str | None = None,
    parallel: int = 1,
) -> None:
    """End-to-end apply for one CLI invocation. Writes to stdout/stderr."""
    if parallel < 1:
        raise typer.BadParameter("--parallel must be >= 1")
    if parallel > APPLY_PARALLEL_CAP:
        typer.echo(
            f"warning: --parallel {parallel} clamped to {APPLY_PARALLEL_CAP} "
            "(matches the HTTP connection pool default)",
            err=True,
        )
    reader = _make_reader(kind_filter=kind_filter, cli_name=cli_name)
    apply_one = _build_apply_resource(ctx)
    outcomes = ApplyFile(apply_one, reader, ctx.catalog, ctx.fk, parallel=parallel)(
        file, write=write, fail_fast=fail_fast
    )
    typer.echo(format_output(outcome_rows(outcomes), fmt=fmt, columns=columns))
    if not write:
        for outcome in outcomes:
            for line in diff_lines(outcome):
                typer.echo(line, err=True)
    if any(o.action == "failed" for o in outcomes):
        raise typer.Exit(code=1)


def _make_reader(*, kind_filter: str | None, cli_name: str | None) -> ResourceDocumentReader:
    """Build a ResourceDocumentReader that optionally filters by kind."""

    def _reader(path: Path) -> Iterable[Resource]:
        docs = list(read_resources(path))
        if kind_filter is None:
            return docs
        wrong = [d for d in docs if d.kind != kind_filter]
        if wrong:
            unique = sorted({d.kind for d in wrong})
            label = cli_name or kind_filter
            typer.echo(
                f"warning: {len(wrong)} doc(s) skipped — wrong kind for "
                f"{label} ({{{','.join(unique)}}})",
                err=True,
            )
        return [d for d in docs if d.kind == kind_filter]

    return _reader


def _build_apply_resource(ctx: AwxContext) -> ApplyResource:
    def _warn(msg: str) -> None:
        typer.echo(f"warning: {msg}", err=True)

    return ApplyResource(
        client=ctx.repo,
        catalog=ctx.catalog,
        fk=ctx.fk,
        strategies=ctx.strategies,
        warn=_warn,
    )


def resolve_apply_file(positional: Path | None, option: Path | None) -> Path:
    """Resolve apply's file target from positional + ``--file`` alias.

    The ``--file`` option wins when both are given — mirrors how a
    typing-twice user expects an explicit flag to override the
    positional. Raises ``typer.BadParameter`` when neither is set.
    """
    target = option if option is not None else positional
    if target is None:
        raise typer.BadParameter(
            "expected a FILE positional argument or --file/-f option",
            param_hint="FILE",
        )
    return target


__all__ = ["resolve_apply_file", "run_apply"]
