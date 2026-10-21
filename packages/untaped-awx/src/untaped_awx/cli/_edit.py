"""Register external-editor batch editing for each writable AWX kind."""

from __future__ import annotations

from typing import Annotated

from cyclopts import App, Parameter

from untaped.sdk import report_errors, writes
from untaped_awx.cli._edit_runner import run_edit
from untaped_awx.cli._mutation_runner import CONTROL_DEFAULTS, WriteControls
from untaped_awx.cli._selection import SELECTION_DEFAULTS, SelectionOptions
from untaped_awx.cli.context import open_context
from untaped_awx.cli.options import NamesArgument
from untaped_awx.infrastructure.resources.spec import AwxResourceSpec


def _add_edit(app: App, spec: AwxResourceSpec) -> None:
    @app.command(name="edit")
    @writes
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
        """Edit selected resources in $VISUAL/$EDITOR, then preview and confirm once.

        The selection opens as one YAML multi-document batch in an editor that
        waits (such as `code --wait`). It needs a real terminal at `/dev/tty`,
        even with piped stdin or --yes; without one, use `patch` or `apply`.
        Removing a document deselects it; removing a field leaves it
        unchanged; a nested value replaces the whole field. Identity, name and
        parent changes, and new or duplicate documents, are refused; `edit`
        never creates or renames. Invalid YAML can be reopened or cancelled,
        and a session with no change neither prompts nor writes. The file is
        owner-only and removed after a clean session; a failed session keeps
        it and prints its path.
        """
        selection.require_source(names)
        with report_errors():
            controls = controls.validated()
            with open_context() as ctx:
                selected = selection.select(ctx, spec, names)
                run_edit(ctx, spec, selected, controls, fields=field)
