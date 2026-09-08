"""Patch existing selected resources through the authoritative mutation engine."""

from pathlib import Path
from typing import Annotated

from cyclopts import App, Parameter

from untaped.api import ColumnsOption, ConfigError, FormatOption, raise_usage, report_errors
from untaped.capabilities.awx.application import SaveResource
from untaped.capabilities.awx.cli._apply_runner import build_apply_resource
from untaped.capabilities.awx.cli._context import open_context
from untaped.capabilities.awx.cli._mutation_runner import run_mutation_plan, validate_controls
from untaped.capabilities.awx.cli._patch_values import build_patch
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
from untaped.capabilities.awx.domain import Resource
from untaped.capabilities.awx.infrastructure.spec import AwxResourceSpec


def _add_patch(app: App, spec: AwxResourceSpec) -> None:
    @app.command(name="patch")
    def patch_command(
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
        set_: Annotated[
            list[str] | None,
            Parameter(
                name="--set",
                consume_multiple=False,
                help="Replace a top-level field KEY=VALUE (repeatable, JSON coerced).",
            ),
        ] = None,
        patch_file: Annotated[
            Path | None,
            Parameter(name="--patch-file", help="YAML/JSON field mapping; --set takes precedence."),
        ] = None,
        yes: YesOption = False,
        dry_run: DryRunOption = False,
        continue_on_error: ContinueOption = False,
        parallel: ParallelOption = 1,
        allow_unverified: UnverifiedOption = False,
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """Replace specified fields on an existing selection, with one confirmation."""
        if not names and not stdin and not filter_ and search is None and not all_:
            raise_usage("provide names, --stdin, filters/search, or --all")
        with report_errors():
            parallel = validate_controls(
                yes=yes, dry_run=dry_run, allow_unverified=allow_unverified, parallel=parallel
            )
            overlay = build_patch(set_, patch_file)
            if not overlay:
                raise ConfigError("provide --set and/or --patch-file with at least one field")
            immutable = {
                "id",
                "name",
                "organization",
                "parent",
                "kind",
                "type",
                "unified_job_template",
            }
            if spec.apply_strategy == "inventory_child":
                immutable.add("inventory")
            if forbidden := immutable.intersection(overlay):
                raise ConfigError(
                    f"patch cannot change identity fields: {', '.join(sorted(forbidden))}"
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
                saver = SaveResource(ctx.repo, ctx.fk)
                resources = [
                    Resource(
                        kind=spec.kind,
                        metadata=saver.metadata_from_record(spec, item.record),
                        spec=overlay,
                    )
                    for item in selected
                ]
                engine = build_apply_resource(ctx, allow_unverified=allow_unverified).engine
                plan = engine.prepare(resources, mode="patch", existing=selected)
                run_mutation_plan(
                    ctx,
                    engine,
                    plan,
                    yes=yes,
                    dry_run=dry_run,
                    continue_on_error=continue_on_error,
                    parallel=parallel,
                    allow_unverified=allow_unverified,
                    fmt=fmt,
                    columns=columns,
                )
