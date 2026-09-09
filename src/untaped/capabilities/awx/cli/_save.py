"""Export complete fixed selections as portable resource documents."""

from pathlib import Path
from typing import Annotated

from cyclopts import App, Parameter

from untaped.api import ColumnsOption, FormatOption, raise_usage, report_errors
from untaped.capabilities.awx.cli._context import open_context
from untaped.capabilities.awx.cli._save_runner import run_save_selection
from untaped.capabilities.awx.cli._selection import select_resources
from untaped.capabilities.awx.cli.options import (
    AllOption,
    ByIdOption,
    FilterOption,
    InventoryOption,
    InventoryOrganizationOption,
    OrganizationOption,
    ParentOption,
    SearchOption,
    StdinOption,
)
from untaped.capabilities.awx.infrastructure.spec import AwxResourceSpec


def _add_save(app: App, spec: AwxResourceSpec) -> None:
    @app.command(name="save")
    def save_command(
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
        output: Annotated[
            Path | None,
            Parameter(name=["--out", "-o"], help="Write portable YAML documents to FILE."),
        ] = None,
        fmt: FormatOption = "yaml",
        columns: ColumnsOption = None,
    ) -> None:
        """Save a fixed selection into one portable YAML document batch."""
        if not names and not stdin and not filter_ and search is None and not all_:
            raise_usage("provide names, --stdin, filters/search, or --all")
        with report_errors(), open_context() as ctx:
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
            run_save_selection(ctx, spec, selected, output=output, fmt=fmt, columns=columns)
