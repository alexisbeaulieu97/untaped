"""``update`` builder for the spec-driven CLI factory (Project SCM sync)."""

from typing import Annotated

from cyclopts import App, Parameter

from untaped.api import (
    ColumnsOption,
    FormatOption,
    emit,
    report_errors,
)
from untaped.capabilities.awx.application import RunAction, WatchJob
from untaped.capabilities.awx.cli._context import open_context, scope_for_command
from untaped.capabilities.awx.cli.options import ByIdOption, OrganizationOption
from untaped.capabilities.awx.infrastructure.spec import AwxResourceSpec


def _add_update(app: App, spec: AwxResourceSpec) -> None:
    # Project's ``update`` declares ``accepts=frozenset()``; no
    # payload-bearing flags exist yet. When one is added, mirror the
    # ``Parameter(show=...)`` narrowing pattern from ``_add_launch``.
    @app.command(name="update")
    def update_command(
        name: Annotated[str, Parameter(help=f"{spec.kind} name.")],
        *,
        by_id: ByIdOption = False,
        organization: OrganizationOption = None,
        wait: Annotated[
            bool,
            Parameter(name="--wait", negative="", help="Block until terminal."),
        ] = False,
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """Trigger an SCM sync (Project)."""
        with report_errors(), open_context() as ctx:
            scope = scope_for_command(ctx, organization, spec)
            job = RunAction(ctx.repo)(spec, name=name, action="update", scope=scope, by_id=by_id)
            if wait:
                job = WatchJob(ctx.repo)(job)
        emit(job, fmt=fmt, columns=columns, kind="awx.job")
