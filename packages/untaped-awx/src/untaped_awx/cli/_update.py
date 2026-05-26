"""``update`` builder for the spec-driven CLI factory (Project SCM sync)."""

from __future__ import annotations

import typer
from untaped_core import (
    ColumnsOption,
    FormatOption,
    format_output,
    report_errors,
)

from untaped_awx.application import RunAction, WatchJob
from untaped_awx.cli._context import open_context, scope_for_command
from untaped_awx.infrastructure.spec import AwxResourceSpec


def _add_update(app: typer.Typer, spec: AwxResourceSpec) -> None:
    # Project's ``update`` declares ``accepts=frozenset()``; no
    # payload-bearing flags exist yet. When one is added, mirror the
    # ``Option(hidden=...)`` narrowing pattern from ``_add_launch``.
    @app.command("update", no_args_is_help=True)
    def update_command(
        name: str = typer.Argument(..., help=f"{spec.kind} name."),
        organization: str | None = typer.Option(
            None, "--organization", help="Scope to organization."
        ),
        wait: bool = typer.Option(False, "--wait", help="Block until terminal."),
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """Trigger an SCM sync (Project)."""
        with report_errors(), open_context() as ctx:
            scope = scope_for_command(ctx, organization, spec)
            job = RunAction(ctx.repo)(spec, name=name, action="update", scope=scope)
            if wait:
                job = WatchJob(ctx.repo)(job)
        typer.echo(format_output([job.model_dump()], fmt=fmt, columns=columns))
