"""Register external-editor batch editing for each writable AWX kind."""

from typing import Annotated

from cyclopts import App, Parameter

from untaped.api import ColumnsOption, FormatOption, raise_usage, report_errors
from untaped.capabilities.awx.cli._context import open_context
from untaped.capabilities.awx.cli._edit_runner import run_edit
from untaped.capabilities.awx.cli._mutation_runner import validate_controls
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
    UnverifiedOption,
    YesOption,
)
from untaped.capabilities.awx.infrastructure.spec import AwxResourceSpec


def _add_edit(app: App, spec: AwxResourceSpec) -> None:
    @app.command(name="edit")
    def edit_command(
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
        field: Annotated[
            list[str] | None,
            Parameter(
                name="--field",
                consume_multiple=False,
                help="Limit editable top-level fields (repeatable).",
            ),
        ] = None,
        yes: YesOption = False,
        dry_run: DryRunOption = False,
        continue_on_error: ContinueOption = False,
        parallel: ParallelOption = 1,
        allow_unverified: UnverifiedOption = False,
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """Edit selected resources in $VISUAL/$EDITOR, then preview and confirm once."""
        if not names and not stdin and not filter_ and search is None and not all_:
            raise_usage("provide names, --stdin, filters/search, or --all")
        with report_errors():
            parallel = validate_controls(
                yes=yes, dry_run=dry_run, allow_unverified=allow_unverified, parallel=parallel
            )
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
                run_edit(
                    ctx,
                    spec,
                    selected,
                    fields=field,
                    yes=yes,
                    dry_run=dry_run,
                    continue_on_error=continue_on_error,
                    parallel=parallel,
                    allow_unverified=allow_unverified,
                    fmt=fmt,
                    columns=columns,
                )
