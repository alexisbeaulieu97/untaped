"""``get`` builder for the spec-driven CLI factory."""

from typing import Annotated

from cyclopts import App, Parameter

from untaped.capabilities.awx.application.mutation_values import redact_value
from untaped.capabilities.awx.application.template_scm import SCM_FIELDS
from untaped.capabilities.awx.application.template_scm import with_scm as add_scm_fields
from untaped.capabilities.awx.cli._selection import select_resources
from untaped.capabilities.awx.cli.context import open_context
from untaped.capabilities.awx.cli.names import name_fks
from untaped.capabilities.awx.cli.options import (
    WITH_SCM_HELP,
    AllOption,
    ByIdOption,
    FilterOption,
    InventoryOption,
    InventoryOrganizationOption,
    NamesArgument,
    OrganizationOption,
    ParentOption,
    SearchOption,
    StdinOption,
    offers_with_scm,
)
from untaped.capabilities.awx.cli.pipe import pipe_kind_for_spec
from untaped.capabilities.awx.infrastructure.spec import AwxResourceSpec
from untaped.sdk import (
    ColumnsOption,
    FormatOption,
    emit,
    raise_usage,
    report_errors,
)


def _add_get(app: App, spec: AwxResourceSpec) -> None:
    # Resolved lazily by cyclopts (PEP 649), so ``show`` can read ``spec``.
    @app.command(name="get")
    def get_command(
        names: NamesArgument = None,
        /,
        *,
        search: SearchOption = None,
        filter_: FilterOption = None,
        all_: AllOption = False,
        parent: ParentOption = None,
        stdin: StdinOption = False,
        organization: OrganizationOption = None,
        inventory: InventoryOption = None,
        inventory_organization: InventoryOrganizationOption = None,
        by_id: ByIdOption = False,
        with_names: Annotated[
            bool,
            Parameter(
                name="--with-names",
                negative="",
                help=(
                    "Replace FK ids with names from summary_fields in every format "
                    "(a table always shows names)."
                ),
            ),
        ] = False,
        with_scm: Annotated[
            bool,
            Parameter(
                name="--with-scm", negative="", show=offers_with_scm(spec), help=WITH_SCM_HELP
            ),
        ] = False,
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """Fetch one or more resources by name, or by explicit AWX id."""
        if not names and not stdin and not filter_ and search is None and not all_:
            raise_usage("provide names, --stdin, filters/search, or --all")
        if with_scm and not offers_with_scm(spec):
            raise_usage(f"--with-scm is not available for {spec.cli_name}")
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
                require_explicit=True,
                organization=organization,
                inventory=inventory,
                inventory_organization=inventory_organization,
                parent=parent,
            )
            records = [item.record for item in selected]
            if with_scm:
                records = add_scm_fields(
                    records,
                    client=ctx.repo,
                    catalog=ctx.catalog,
                    warn=lambda msg: ctx.progress_ui().message("warning", msg),
                )
        if records:
            default_cols = (*spec.list_columns, *SCM_FIELDS) if with_scm else spec.list_columns
            table = fmt == "table"
            records = name_fks(
                records,
                spec,
                with_names=with_names,
                table=table,
                columns=columns,
                defaults=default_cols if table else (),
            )
            records = [
                redact_value(record, spec.secret_paths, skip_empty=True) for record in records
            ]
            emit(
                records,
                fmt=fmt,
                columns=columns,
                table_columns=default_cols,
                kind=pipe_kind_for_spec(spec),
            )
        if not records:
            emit(
                [], fmt=fmt, kind=pipe_kind_for_spec(spec), empty=f"No matching {spec.kind} found."
            )
