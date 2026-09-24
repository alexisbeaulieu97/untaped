"""Declarative file/directory apply command for writable resource kinds."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from cyclopts import App, Parameter

from untaped.capabilities.awx.cli._apply_runner import run_apply
from untaped.capabilities.awx.cli._mutation_runner import CONTROL_DEFAULTS, WriteControls
from untaped.capabilities.awx.cli.context import open_context
from untaped.capabilities.awx.infrastructure.spec import AwxResourceSpec
from untaped.capability_api import report_errors


def _add_apply(app: App, spec: AwxResourceSpec) -> None:
    @app.command(name="apply")
    def apply_command(
        file: Annotated[Path, Parameter(help="YAML file or directory.")],
        /,
        *,
        controls: WriteControls = CONTROL_DEFAULTS,
    ) -> None:
        """Create/update a complete YAML file or directory, with one confirmation."""
        with report_errors():
            controls = controls.validated()
            with open_context() as ctx:
                run_apply(ctx, file, controls, kind_filter=spec.kind)
