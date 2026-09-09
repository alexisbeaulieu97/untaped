"""Declarative file/directory apply command for writable resource kinds."""

from pathlib import Path

from cyclopts import App

from untaped.api import ColumnsOption, FormatOption, report_errors
from untaped.capabilities.awx.cli._apply_runner import run_apply
from untaped.capabilities.awx.cli._context import open_context
from untaped.capabilities.awx.cli._mutation_runner import validate_controls
from untaped.capabilities.awx.cli.options import (
    ContinueOption,
    DryRunOption,
    ParallelOption,
    UnverifiedOption,
    YesOption,
)
from untaped.capabilities.awx.infrastructure.spec import AwxResourceSpec


def _add_apply(app: App, spec: AwxResourceSpec) -> None:
    @app.command(name="apply")
    def apply_command(
        file: Path,
        /,
        *,
        yes: YesOption = False,
        dry_run: DryRunOption = False,
        continue_on_error: ContinueOption = False,
        parallel: ParallelOption = 1,
        allow_unverified: UnverifiedOption = False,
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """Create/update a complete YAML file or directory, with one confirmation."""
        with report_errors():
            parallel = validate_controls(
                yes=yes, dry_run=dry_run, allow_unverified=allow_unverified, parallel=parallel
            )
            with open_context() as ctx:
                run_apply(
                    ctx,
                    file,
                    yes=yes,
                    dry_run=dry_run,
                    continue_on_error=continue_on_error,
                    parallel=parallel,
                    allow_unverified=allow_unverified,
                    kind_filter=spec.kind,
                    fmt=fmt,
                    columns=columns,
                )
