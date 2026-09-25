"""``<kind> rename SOURCE NEW``: one verified rename with a preview."""

from __future__ import annotations

from typing import Annotated

from cyclopts import App, Parameter

from untaped.capabilities.awx.application.rename_resource import RenameResource
from untaped.capabilities.awx.cli._mutation_runner import confirm_batch
from untaped.capabilities.awx.cli._selection import select_resources
from untaped.capabilities.awx.cli.context import open_context
from untaped.capabilities.awx.cli.format import format_scope
from untaped.capabilities.awx.cli.options import (
    ByIdOption,
    DryRunOption,
    OrganizationOption,
    YesOption,
)
from untaped.capabilities.awx.errors import BadRequestError
from untaped.capabilities.awx.infrastructure.spec import AwxResourceSpec
from untaped.capability_api import (
    ColumnsOption,
    FormatOption,
    echo,
    emit,
    q,
    report_errors,
)


def _add_rename(app: App, spec: AwxResourceSpec) -> None:
    @app.command(name="rename")
    def rename_command(
        source: Annotated[
            str, Parameter(help="Current name of the resource, or its AWX id with --by-id.")
        ],
        new_name: Annotated[str, Parameter(name="NEW", help="New name for the resource.")],
        /,
        *,
        organization: OrganizationOption = None,
        by_id: ByIdOption = False,
        yes: YesOption = False,
        dry_run: DryRunOption = False,
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """Rename one resource in place, after a preview, and verify the new name."""
        with report_errors(), open_context() as ctx:
            (selected,) = select_resources(
                ctx, spec, [source], by_id=by_id, organization=organization
            )
            renamer = RenameResource(ctx.repo)
            plan = renamer.plan(spec, selected, new_name)
            echo(
                f"Rename {spec.kind} id={selected.id} scope={format_scope(selected.scope)}: "
                f"{q(selected.name or '')} -> {q(new_name)}",
                err=True,
            )
            outcome = plan.outcome(spec, action="planned")
            if confirm_batch(ctx, count=1, verb="rename", yes=yes, dry_run=dry_run):
                outcome = renamer(spec, plan)
            emit(outcome, fmt=fmt, columns=columns, kind="awx.rename_outcome")
            if outcome.failed:
                raise BadRequestError(f"{spec.kind}#{outcome.id}: {outcome.detail}")
