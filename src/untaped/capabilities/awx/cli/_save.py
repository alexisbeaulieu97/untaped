"""``save`` builder for the spec-driven CLI factory."""

from pathlib import Path
from typing import Annotated

from cyclopts import App, Parameter

from untaped.api import (
    ColumnsOption,
    FormatOption,
    report_errors,
)
from untaped.capabilities.awx.cli._context import open_context, scope_for_command
from untaped.capabilities.awx.cli._save_runner import run_save_one
from untaped.capabilities.awx.cli.options import (
    InventoryOption,
    InventoryOrganizationOption,
    OrganizationOption,
)
from untaped.capabilities.awx.infrastructure.spec import AwxResourceSpec


def _add_save(app: App, spec: AwxResourceSpec) -> None:
    @app.command(name="save")
    def save_command(
        name: Annotated[str, Parameter(help=f"{spec.kind} name.")],
        *,
        output: Annotated[
            Path | None,
            Parameter(name=["--out", "-o"], help="Write to FILE; default is stdout."),
        ] = None,
        organization: OrganizationOption = None,
        inventory: InventoryOption = None,
        inventory_organization: InventoryOrganizationOption = None,
        fmt: FormatOption = "yaml",
        columns: ColumnsOption = None,
    ) -> None:
        """Dump the resource as a portable YAML envelope.

        Default ``--format yaml`` emits the bare envelope so the output
        pipes straight into ``apply`` (multi-doc mapping shape that
        ``read_resources`` ingests). Non-yaml formats go through
        row rendering for a one-row projection that matches the
        suite-wide ``--columns`` contract. ``--columns`` applies to
        non-yaml formats only — yaml emits the bare envelope unfiltered
        so the round-trip into ``apply`` stays intact.
        """
        with report_errors(), open_context() as ctx:
            scope = scope_for_command(
                ctx,
                organization,
                spec,
                inventory=inventory,
                inventory_organization=inventory_organization,
            )
            run_save_one(
                ctx,
                spec,
                name=name,
                scope=scope,
                output=output,
                fmt=fmt,
                columns=columns,
            )
