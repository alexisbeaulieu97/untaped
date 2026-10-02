"""Export complete fixed selections as portable resource documents."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from cyclopts import App, Parameter

from untaped.capabilities.awx.cli._selection import SELECTION_DEFAULTS, SelectionOptions
from untaped.capabilities.awx.cli.context import open_context
from untaped.capabilities.awx.cli.options import NamesArgument
from untaped.capabilities.awx.cli.save_runner import run_save_selection
from untaped.capabilities.awx.infrastructure.spec import AwxResourceSpec
from untaped.capability_api import ColumnsOption, FormatOption, UsageError, report_errors


def _add_save(app: App, spec: AwxResourceSpec) -> None:
    @app.command(name="export")
    def export_command(
        names: NamesArgument = None,
        /,
        *,
        selection: SelectionOptions = SELECTION_DEFAULTS,
        output: Annotated[
            Path | None,
            Parameter(name=["--out", "-o"], help="Write portable YAML documents to FILE."),
        ] = None,
        fmt: FormatOption = "yaml",
        columns: ColumnsOption = None,
        comment: Annotated[
            str | None,
            Parameter(
                name="--comment", help="Add TEXT as a '#' comment at the top of each document."
            ),
        ] = None,
    ) -> None:
        """Export a fixed selection as one portable YAML document batch."""
        selection.require_source(names)
        with report_errors():
            if comment and output is None and fmt != "yaml":
                raise UsageError("--comment needs YAML output; drop --format or pass --out FILE")
            with open_context() as ctx:
                selected = selection.select(ctx, spec, names)
                run_save_selection(
                    ctx, spec, selected, output=output, fmt=fmt, columns=columns, comment=comment
                )
