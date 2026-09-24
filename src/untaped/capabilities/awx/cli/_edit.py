"""Register external-editor batch editing for each writable AWX kind."""

from __future__ import annotations

from typing import Annotated

from cyclopts import App, Parameter

from untaped.capabilities.awx.cli._edit_runner import run_edit
from untaped.capabilities.awx.cli._mutation_runner import CONTROL_DEFAULTS, WriteControls
from untaped.capabilities.awx.cli._selection import SELECTION_DEFAULTS, SelectionOptions
from untaped.capabilities.awx.cli.context import open_context
from untaped.capabilities.awx.cli.options import NamesArgument
from untaped.capabilities.awx.infrastructure.spec import AwxResourceSpec
from untaped.capability_api import report_errors


def _add_edit(app: App, spec: AwxResourceSpec) -> None:
    @app.command(name="edit")
    def edit_command(
        names: NamesArgument = None,
        /,
        *,
        selection: SelectionOptions = SELECTION_DEFAULTS,
        field: Annotated[
            list[str] | None,
            Parameter(
                name="--field",
                consume_multiple=False,
                negative="",
                help="Limit editable top-level fields (repeatable).",
            ),
        ] = None,
        controls: WriteControls = CONTROL_DEFAULTS,
    ) -> None:
        """Edit selected resources in $VISUAL/$EDITOR, then preview and confirm once."""
        selection.require_source(names)
        with report_errors():
            controls = controls.validated()
            with open_context() as ctx:
                selected = selection.select(ctx, spec, names)
                run_edit(ctx, spec, selected, controls, fields=field)
