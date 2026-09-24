"""``<kind> copy SOURCE --name NEW``: one server-side copy with a preview."""

from __future__ import annotations

from typing import Annotated

from cyclopts import App, Parameter

from untaped.capabilities.awx.application.copy_resource import CopyResource
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
from untaped.capabilities.awx.domain import CopyOutcome
from untaped.capabilities.awx.infrastructure.spec import AwxResourceSpec
from untaped.capability_api import (
    ColumnsOption,
    FormatOption,
    echo,
    emit,
    q,
    report_errors,
)


def _add_copy(app: App, spec: AwxResourceSpec) -> None:
    @app.command(name="copy")
    def copy_command(
        source: Annotated[
            str, Parameter(help="Name of the resource to copy, or its AWX id with --by-id.")
        ],
        /,
        *,
        name: Annotated[str, Parameter(name="--name", help="Name of the new copy.")],
        organization: OrganizationOption = None,
        by_id: ByIdOption = False,
        yes: YesOption = False,
        dry_run: DryRunOption = False,
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """Copy one resource under a new name in the same scope, after a preview."""
        with report_errors(), open_context() as ctx:
            (selected,) = select_resources(
                ctx, spec, [source], by_id=by_id, organization=organization
            )
            copier = CopyResource(ctx.repo)
            plan = copier.plan(spec, selected, name)
            echo(
                f"Copy {spec.kind}/{selected.name} id={selected.id} "
                f"scope={format_scope(selected.scope)} -> {q(name)}",
                err=True,
            )
            for part in plan.not_carried:
                ctx.progress_ui().message("warning", f"AWX will not copy {part}")
            outcome = CopyOutcome(
                name=name,
                source_id=selected.id,
                kind=spec.kind,
                action="planned",
                not_carried=list(plan.not_carried),
            )
            if confirm_batch(ctx, count=1, verb="copy", yes=yes, dry_run=dry_run):
                outcome = copier(spec, plan)
            emit(outcome, fmt=fmt, columns=columns, kind="awx.copy_outcome")
