"""``list`` builder for the spec-driven CLI factory."""

from contextlib import nullcontext
from typing import Annotated

from cyclopts import App, Parameter

from untaped.api import (
    ColumnsOption,
    FormatOption,
    emit,
    raise_usage,
    report_errors,
)
from untaped.capabilities.awx.cli._context import open_context
from untaped.capabilities.awx.cli._names import flatten_fks
from untaped.capabilities.awx.cli._pipe import pipe_kind_for_spec
from untaped.capabilities.awx.cli._selection import select_resources
from untaped.capabilities.awx.cli.options import (
    AllOption,
    ByIdOption,
    InventoryOption,
    InventoryOrganizationOption,
    OrganizationOption,
    ParentOption,
    StdinOption,
)
from untaped.capabilities.awx.infrastructure.spec import AwxResourceSpec


def _add_list(app: App, spec: AwxResourceSpec) -> None:
    @app.command(name="list")
    def list_command(
        names: list[str] | None = None,
        *,
        all_: AllOption = False,
        parent: ParentOption = None,
        search: Annotated[
            str | None,
            Parameter(name="--search", help="Fuzzy server-side search."),
        ] = None,
        filter_: Annotated[
            list[str] | None,
            Parameter(
                name="--filter",
                help=(
                    "Server-side filter, KEY=VALUE (repeatable). Passed verbatim to "
                    "AWX, so any Django-style lookup works: --filter "
                    "organization__name=Default --filter name__icontains=deploy."
                ),
                consume_multiple=False,
            ),
        ] = None,
        limit: Annotated[int | None, Parameter(name="--limit", help="Cap result count.")] = None,
        stdin: StdinOption = False,
        by_id: ByIdOption = False,
        organization: OrganizationOption = None,
        inventory: InventoryOption = None,
        inventory_organization: InventoryOrganizationOption = None,
        with_names: Annotated[
            bool,
            Parameter(
                name="--with-names",
                negative="",
                help=(
                    "Replace FK ids with names from summary_fields. Multi-valued "
                    "FKs (e.g. credentials) become lists of names."
                ),
            ),
        ] = False,
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """List a complete selection by names, IDs, typed input, or query."""
        if limit is not None and limit < 0:
            raise_usage("--limit must be non-negative")
        with report_errors(), open_context() as ctx:
            with (
                ctx.progress_ui().progress(f"Loading {spec.kind}…")
                if not stdin and not names
                else nullcontext()
            ):
                selected = select_resources(
                    ctx,
                    spec,
                    names,
                    stdin=stdin,
                    by_id=by_id,
                    filters=filter_,
                    search=search,
                    all_=all_,
                    default_all=True,
                    organization=organization,
                    inventory=inventory,
                    inventory_organization=inventory_organization,
                    parent=parent,
                )
            records = [item.record for item in selected]
            if limit is not None:
                records = records[:limit]
        cols = list(columns) if columns else list(spec.list_columns)
        if with_names:
            # Pass ``cols`` so display-only FK columns (e.g. Host's
            # ``inventory``, which lives in ``read_only_fields`` rather
            # than ``fk_refs``) get flattened from ``summary_fields``.
            records = flatten_fks(records, spec, columns=cols)
        emit(records, fmt=fmt, columns=cols, kind=pipe_kind_for_spec(spec), empty=False)
