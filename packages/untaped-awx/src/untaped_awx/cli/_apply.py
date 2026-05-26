"""``apply`` builder for the spec-driven CLI factory (per-kind, single file)."""

from __future__ import annotations

from pathlib import Path

import typer
from untaped_core import ColumnsOption, OutputFormat, report_errors

from untaped_awx.application.apply_file import APPLY_PARALLEL_CAP
from untaped_awx.cli._apply_runner import resolve_apply_file, run_apply
from untaped_awx.cli._context import open_context
from untaped_awx.infrastructure.spec import AwxResourceSpec


def _add_apply(app: typer.Typer, spec: AwxResourceSpec) -> None:
    @app.command("apply", no_args_is_help=True)
    def apply_command(
        file: Path | None = typer.Argument(None, help="YAML file to apply."),
        file_opt: Path | None = typer.Option(
            None,
            "--file",
            "-f",
            help="Deprecated alias; prefer the positional FILE.",
        ),
        yes: bool = typer.Option(False, "--yes", help="Actually write (default is preview only)."),
        fail_fast: bool = typer.Option(False, "--fail-fast", help="Abort on first error."),
        parallel: int = typer.Option(
            1,
            "--parallel",
            "-j",
            help=(
                "Concurrent doc writes within this kind. Phase 2 (membership) "
                f"stays serial. Capped at {APPLY_PARALLEL_CAP} "
                "(matches the HTTP connection pool default)."
            ),
        ),
        fmt: OutputFormat = typer.Option("table", "--format", help="Output format."),
        columns: ColumnsOption = None,
    ) -> None:
        """Apply a YAML file. Default is preview-only — pass ``--yes`` to write.

        Wrong-kind docs in the file are warned about and **never written** —
        this command is scoped to the kind of its parent sub-app.
        """
        target = resolve_apply_file(file, file_opt)
        with report_errors(), open_context() as ctx:
            run_apply(
                ctx,
                target,
                write=yes,
                fail_fast=fail_fast,
                fmt=fmt,
                columns=columns,
                kind_filter=spec.kind,
                cli_name=spec.cli_name,
                parallel=parallel,
            )
