"""Fixed-selection deletion with whole-batch validation and strict receipts."""

from __future__ import annotations

from cyclopts import App

from untaped.sdk import (
    ColumnsOption,
    FormatOption,
    emit,
    finish,
    report_errors,
    writes,
)
from untaped_awx.application import DeleteResource
from untaped_awx.application.jobs.selected_actions import run_selected_actions
from untaped_awx.application.resources.mutation_values import redact_error
from untaped_awx.cli._mutation_runner import confirm_batch, validate_controls
from untaped_awx.cli._selection import (
    SELECTION_DEFAULTS,
    SelectionOptions,
    select_resources,
)
from untaped_awx.cli.context import open_context
from untaped_awx.cli.format import format_scope
from untaped_awx.cli.options import (
    DryRunOption,
    FailFastOption,
    NamesArgument,
    ParallelOption,
    YesOption,
    offers_usage,
)
from untaped_awx.infrastructure.resources.spec import AwxResourceSpec
from untaped_awx.records import DeleteOutcome


def _add_delete(app: App, spec: AwxResourceSpec) -> None:
    help_text = (
        "Delete an explicitly selected batch after one complete preview.\n\n"
        "What a deleted resource loses: `untaped agents awx writes`."
    )
    if offers_usage(spec):
        help_text += (
            " A workflow node that ran a deleted template runs nothing afterwards: "
            "`usage` lists the workflows that contain it."
        )
    if spec.kind == "Inventory":
        help_text += " Deleting an inventory is asynchronous (`deletion_requested`)."

    @app.command(name="delete", help=help_text)
    @writes(destructive=True)
    def delete_command(
        names: NamesArgument = None,
        /,
        *,
        selection: SelectionOptions = SELECTION_DEFAULTS,
        yes: YesOption = False,
        dry_run: DryRunOption = False,
        fail_fast: FailFastOption = False,
        parallel: ParallelOption = 1,
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
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
                    ctx.diag.info(
                        f"Delete {item.kind}/{item.name} id={item.id} "
                        f"scope={format_scope(item.scope)}",
                        unmuted=True,
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
                        fail_fast=fail_fast,
                        error_detail=lambda exc, item: redact_error(exc, spec, item.record),
                    )
                    for row, outcome in zip(rows, outcomes, strict=True):
                        if outcome.action == "completed":
                            assert outcome.result is not None
                            row["action"] = outcome.result.action
                        else:
                            row["action"] = outcome.action
                        row["detail"] = outcome.detail
                        row.update(outcome.row_error())
                        label = f"{outcome.target.kind}#{outcome.target.id}"
                        if outcome.failure is not None:
                            ctx.diag.failure(outcome.failure, item=label)
                        elif outcome.detail:
                            ctx.diag.warning(f"{label}: {outcome.detail}")
                    failed = any(outcome.action != "completed" for outcome in outcomes)
                emit(
                    [DeleteOutcome.model_validate(row) for row in rows],
                    fmt=fmt,
                    columns=columns,
                    table_columns=DeleteOutcome.table_columns,
                )
                finish(failed)
