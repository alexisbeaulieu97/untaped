"""``<kind> copy SOURCE --name NEW``: one server-side copy with a preview."""

from __future__ import annotations

from typing import Annotated

from cyclopts import App, Parameter

from untaped.sdk import (
    ColumnsOption,
    FormatOption,
    emit,
    q,
    report_errors,
    writes,
)
from untaped_awx.application.resources.copy_resource import CopyResource
from untaped_awx.cli._mutation_runner import confirm_batch
from untaped_awx.cli._selection import select_resources
from untaped_awx.cli.context import open_context
from untaped_awx.cli.format import format_scope
from untaped_awx.cli.options import (
    ByIdOption,
    DryRunOption,
    OrganizationOption,
    YesOption,
)
from untaped_awx.domain import CopyOutcome
from untaped_awx.infrastructure.resources.spec import AwxResourceSpec


def _add_copy(app: App, spec: AwxResourceSpec) -> None:
    @app.command(name="copy")
    @writes
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
        """Copy one resource under a new name in the same scope, after a preview.

        AWX copies it server-side in the source's organization. A taken name,
        the source's own name, or a source AWX cannot copy (`can_copy: false`)
        is refused before writing. A warning names the parts AWX will not carry
        without user input, such as a workflow's references the caller cannot
        use; the `awx.copy_outcome` row lists them in `not_carried`, and its
        `id` is the new template's. To copy to another controller, export,
        change `metadata.name` and apply (`untaped agents awx documents`).

        Examples
        --------
        ```console
        $ untaped awx job-templates copy Deploy --name "Deploy next" --org Default --dry-run
        ```
        """
        with report_errors(), open_context() as ctx:
            (selected,) = select_resources(
                ctx, spec, [source], by_id=by_id, organization=organization
            )
            copier = CopyResource(ctx.repo)
            plan = copier.plan(spec, selected, name)
            ctx.diag.info(
                f"Copy {spec.kind}/{selected.name} id={selected.id} "
                f"scope={format_scope(selected.scope)} -> {q(name)}",
                unmuted=True,
            )
            for part in plan.not_carried:
                ctx.diag.warning(f"AWX will not copy {part}")
            outcome = CopyOutcome(
                name=name,
                source_id=selected.id,
                kind=spec.kind,
                action="planned",
                not_carried=list(plan.not_carried),
            )
            if confirm_batch(ctx, count=1, verb="copy", yes=yes, dry_run=dry_run):
                outcome = copier(spec, plan)
            emit(outcome, fmt=fmt, columns=columns)
