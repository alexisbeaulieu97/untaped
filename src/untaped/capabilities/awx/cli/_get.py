"""``get`` builder for the spec-driven CLI factory.

Also owns ``default_get_columns`` — the public helper shared with
``cli/unified_templates_commands.py`` so the polymorphic browser
projects records the same way as factory-built ``get``.
"""

from collections.abc import Sequence
from typing import Annotated

from cyclopts import App, Parameter

from untaped.api import (
    ColumnsOption,
    FormatOption,
    OutputFormat,
    emit,
    raise_usage,
    report_errors,
)
from untaped.capabilities.awx.application.mutation_values import redact_value
from untaped.capabilities.awx.cli._context import open_context
from untaped.capabilities.awx.cli._names import flatten_fks
from untaped.capabilities.awx.cli._pipe import pipe_kind_for_spec
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


def _add_get(app: App, spec: AwxResourceSpec) -> None:
    @app.command(name="get")
    def get_command(
        names: Annotated[list[str] | None, Parameter(help=f"{spec.kind} name(s).")] = None,
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
                help="Replace FK ids with names from summary_fields.",
            ),
        ] = False,
        fmt: FormatOption = "yaml",
        columns: ColumnsOption = None,
    ) -> None:
        """Fetch one or more resources by name, or by explicit AWX id."""
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
            records = [item.record for item in selected]
        if records:
            cols = list(columns) if columns else default_get_columns(fmt, spec.list_columns)
            if with_names:
                # ``cols`` may be ``None`` for non-table formats — that's
                # fine; ``flatten_fks`` then only flattens declared fk_refs.
                records = flatten_fks(records, spec, columns=cols)
            records = [redact_value(record, spec.secret_paths) for record in records]
            emit(records, fmt=fmt, columns=cols, kind=pipe_kind_for_spec(spec))
        if not records:
            emit(
                [], fmt=fmt, kind=pipe_kind_for_spec(spec), empty=f"No matching {spec.kind} found."
            )


def default_get_columns(fmt: OutputFormat, default_cols: Sequence[str]) -> list[str] | None:
    """Default column projection for ``get`` commands.

    Table needs a projection — a full AWX record (50+ fields) renders as
    an unreadable wall. raw stays one-column-per-line so pipelines that
    do ``get --format raw | …`` keep their established shape; yaml/json
    keep the full record so users can inspect every field. Reused by
    ``unified-templates get`` so the polymorphic browser shares the
    same logic without duplicating it.
    """
    if fmt == "table":
        return list(default_cols)
    return None


__all__ = ["default_get_columns"]
