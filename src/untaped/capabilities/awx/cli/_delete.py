"""Fixed-selection deletion with whole-batch validation and strict receipts."""

from cyclopts import App

from untaped.api import ColumnsOption, FormatOption, echo, emit, finish, raise_usage, report_errors
from untaped.capabilities.awx.application import DeleteResource
from untaped.capabilities.awx.application.mutation_values import redact_error
from untaped.capabilities.awx.application.selected_actions import run_selected_actions
from untaped.capabilities.awx.cli._context import open_context
from untaped.capabilities.awx.cli._mutation_runner import confirm_batch, validate_controls
from untaped.capabilities.awx.cli._selection import select_resources
from untaped.capabilities.awx.cli.options import (
    AllOption,
    ByIdOption,
    ContinueOption,
    DryRunOption,
    FilterOption,
    InventoryOption,
    InventoryOrganizationOption,
    OrganizationOption,
    ParallelOption,
    ParentOption,
    SearchOption,
    StdinOption,
    YesOption,
)
from untaped.capabilities.awx.infrastructure.spec import AwxResourceSpec


def _add_delete(app: App, spec: AwxResourceSpec) -> None:
    @app.command(name="delete")
    def delete_command(
        names: list[str] | None = None,
        *,
        stdin: StdinOption = False,
        by_id: ByIdOption = False,
        search: SearchOption = None,
        filter_: FilterOption = None,
        all_: AllOption = False,
        organization: OrganizationOption = None,
        inventory: InventoryOption = None,
        inventory_organization: InventoryOrganizationOption = None,
        parent: ParentOption = None,
        yes: YesOption = False,
        dry_run: DryRunOption = False,
        continue_on_error: ContinueOption = False,
        parallel: ParallelOption = 1,
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """Delete an explicitly selected batch after one complete preview."""
        if not names and not stdin and not filter_ and search is None and not all_:
            raise_usage("provide names, --stdin, filters/search, or --all")
        with report_errors():
            parallel = validate_controls(yes=yes, dry_run=dry_run, parallel=parallel)
            with open_context() as ctx:
                selected = select_resources(
                    ctx,
                    spec,
                    names,
                    stdin=stdin,
                    by_id=by_id,
                    filters=filter_,
                    search=search,
                    all_=all_,
                    mutation=True,
                    organization=organization,
                    inventory=inventory,
                    inventory_organization=inventory_organization,
                    parent=parent,
                )
                deleter = DeleteResource(ctx.repo)
                deleter.validate_selection(spec, selected)
                rows = [
                    {
                        "id": item.id,
                        "kind": item.kind,
                        "name": item.name,
                        "scope": item.scope,
                        "action": "preview",
                    }
                    for item in selected
                ]
                for row in rows:
                    echo(
                        f"Delete {row['kind']}/{row['name']} id={row['id']} scope={row['scope']}",
                        err=True,
                    )
                failed = False
                if confirm_batch(ctx, count=len(selected), verb="delete", yes=yes, dry_run=dry_run):
                    # Scope, existence, and lifecycle policy are all checked again
                    # for the complete fixed set after confirmation, before writes.
                    for item in selected:
                        select_resources(ctx, spec, [str(item.id)], by_id=True, scope=item.scope)
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
