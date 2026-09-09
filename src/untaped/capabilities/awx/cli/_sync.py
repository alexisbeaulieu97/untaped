"""Shared-selection sync commands for projects, sources and inventories."""

from typing import Annotated

from cyclopts import App, Parameter

from untaped.api import ColumnsOption, FormatOption, report_errors
from untaped.capabilities.awx.cli._action_runner import run_action_selection
from untaped.capabilities.awx.cli._context import open_context
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
)
from untaped.capabilities.awx.infrastructure.spec import AwxResourceSpec


def _add_sync(app: App, spec: AwxResourceSpec) -> None:
    @app.command(name="sync")
    def sync_command(
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
        dry_run: DryRunOption = False,
        continue_on_error: ContinueOption = False,
        parallel: ParallelOption = 1,
        wait: Annotated[
            bool, Parameter(negative="", help="Wait for success; fail on unsuccessful execution.")
        ] = False,
        track: Annotated[
            bool,
            Parameter(
                name=["--track", "-t"],
                negative="",
                help="Stream events while waiting; fail on unsuccessful execution.",
            ),
        ] = False,
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """Sync a fixed selection; inventories expand to their current source IDs."""
        with report_errors():
            parallel = validate_controls(yes=False, dry_run=dry_run, parallel=parallel)
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
                run_action_selection(
                    ctx,
                    spec,
                    selected,
                    action="sync",
                    dry_run=dry_run,
                    parallel=parallel,
                    continue_on_error=continue_on_error,
                    wait=wait,
                    track=track,
                    fmt=fmt,
                    columns=columns,
                )
