"""Fixed-selection deletion with whole-batch validation and strict receipts."""

from __future__ import annotations

from cyclopts import App

from untaped.capabilities.awx.application import DeleteResource
from untaped.capabilities.awx.application.mutation_values import redact_error
from untaped.capabilities.awx.application.selected_actions import run_selected_actions
from untaped.capabilities.awx.cli._mutation_runner import confirm_batch, validate_controls
from untaped.capabilities.awx.cli._selection import (
    SELECTION_DEFAULTS,
    SelectionOptions,
    select_resources,
)
from untaped.capabilities.awx.cli.context import open_context
from untaped.capabilities.awx.cli.format import format_scope
from untaped.capabilities.awx.cli.options import (
    ContinueOption,
    DryRunOption,
    NamesArgument,
    ParallelOption,
    YesOption,
)
from untaped.capabilities.awx.infrastructure.spec import AwxResourceSpec
from untaped.capability_api import (
    ColumnsOption,
    FormatOption,
    echo,
    emit,
    finish,
    report_errors,
)


def _add_delete(app: App, spec: AwxResourceSpec) -> None:
    @app.command(name="delete")
    def delete_command(
        names: NamesArgument = None,
        /,
        *,
        selection: SelectionOptions = SELECTION_DEFAULTS,
        yes: YesOption = False,
        dry_run: DryRunOption = False,
        continue_on_error: ContinueOption = False,
        parallel: ParallelOption = 1,
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """Delete an explicitly selected batch after one complete preview."""
        selection.require_source(names)
        with report_errors():
            parallel = validate_controls(yes=yes, dry_run=dry_run, parallel=parallel)
            with open_context() as ctx:
                selected = selection.select(ctx, spec, names)
                deleter = DeleteResource(ctx.repo)
                deleter.validate_selection(spec, selected)
                rows = [
                    {
                        "id": item.id,
                        "kind": item.kind,
                        "name": item.name,
                        "scope": item.scope,
                        "action": "planned",
                    }
                    for item in selected
                ]
                for item in selected:
                    echo(
                        f"Delete {item.kind}/{item.name} id={item.id} "
                        f"scope={format_scope(item.scope)}",
                        err=True,
                    )
                failed = False
                if confirm_batch(ctx, count=len(selected), verb="delete", yes=yes, dry_run=dry_run):
                    if not yes:
                        # A prompt left time for changes: scope, existence and
                        # lifecycle policy are checked again for the complete
                        # fixed set, in one resolve that shares ancestor reads.
                        selected = select_resources(
                            ctx,
                            spec,
                            [str(item.id) for item in selected],
                            by_id=True,
                            scope=selected[0].scope,
                        )
                        deleter.validate_selection(spec, selected)
                    outcomes = run_selected_actions(
                        selected,
                        lambda item: deleter(spec, item.id),
                        parallel=parallel,
                        continue_on_error=continue_on_error,
                        error_detail=lambda exc, item: redact_error(exc, spec, item.record),
                    )
                    for row, outcome in zip(rows, outcomes, strict=True):
                        if outcome.action == "completed":
                            assert outcome.result is not None
                            row["action"] = outcome.result.action
                        else:
                            row["action"] = outcome.action
                        row["detail"] = outcome.detail
                        if outcome.detail:
                            echo(
                                f"{outcome.action}: {outcome.target.kind}#{outcome.target.id}: "
                                f"{outcome.detail}",
                                err=True,
                            )
                    failed = any(outcome.action != "completed" for outcome in outcomes)
                emit(rows, fmt=fmt, columns=columns, kind="awx.delete_outcome")
                finish(failed)
