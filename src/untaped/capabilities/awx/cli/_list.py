"""``list`` builder for the spec-driven CLI factory."""

from collections.abc import Sequence
from contextlib import nullcontext
from typing import Annotated

from cyclopts import App, Parameter

from untaped.capabilities.awx.application.mutation_values import redact_value
from untaped.capabilities.awx.cli._selection import select_resources
from untaped.capabilities.awx.cli.context import open_context
from untaped.capabilities.awx.cli.names import flatten_fks
from untaped.capabilities.awx.cli.options import (
    AllOption,
    ByIdOption,
    InventoryOption,
    InventoryOrganizationOption,
    NamesArgument,
    OrganizationOption,
    ParentOption,
    StdinOption,
)
from untaped.capabilities.awx.cli.pipe import pipe_kind_for_spec
from untaped.capabilities.awx.infrastructure.spec import AwxResourceSpec
from untaped.capability_api import (
    ColumnsOption,
    FormatOption,
    OutputFormat,
    emit,
    raise_usage,
    report_errors,
)


def _add_list(app: App, spec: AwxResourceSpec) -> None:
    @app.command(name="list")
    def list_command(
        names: NamesArgument = None,
        /,
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
                negative="",
            ),
        ] = None,
        limit: Annotated[
            int | None, Parameter(name="--limit", help="Cap result count (0 = no limit).")
        ] = None,
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
        # ``--limit 0`` means "no limit", as it does for ``jobs list``.
        limit = limit or None
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
                    limit=limit,
                )
            records = [item.record for item in selected]
            if limit is not None:
                records = records[:limit]
        # Default columns shape the human views; json/yaml/pipe keep full records.
        cols = list(columns) if columns else _default_list_columns(fmt, spec.list_columns)
        if with_names:
            # Pass ``cols`` so display-only FK columns (e.g. Host's
            # ``inventory``, which lives in ``read_only_fields`` rather
            # than ``fk_refs``) get flattened from ``summary_fields``.
            records = flatten_fks(records, spec, columns=cols)
        records = [redact_value(record, spec.secret_paths) for record in records]
        emit(records, fmt=fmt, columns=cols, kind=pipe_kind_for_spec(spec), empty=False)


def _default_list_columns(fmt: OutputFormat, default_cols: Sequence[str]) -> list[str] | None:
    """``list`` projects its default columns for ``table`` and ``raw`` only."""
    return list(default_cols) if fmt in {"table", "raw"} else None
