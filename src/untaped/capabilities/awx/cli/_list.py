"""``list`` builder for the spec-driven CLI factory."""

from contextlib import nullcontext
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
    InventoryOption,
    InventoryOrganizationOption,
    NamesArgument,
    OrganizationOption,
    ParentOption,
    StdinOption,
    offers_with_scm,
)
from untaped.capabilities.awx.cli.pipe import pipe_kind_for_spec
from untaped.capabilities.awx.infrastructure.spec import AwxResourceSpec
from untaped.capability_api import (
    ColumnsOption,
    FormatOption,
    emit,
    raise_usage,
    report_errors,
)


def _add_list(app: App, spec: AwxResourceSpec) -> None:
    # Resolved lazily by cyclopts (PEP 649), so ``show`` can read ``spec``.
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
                    "Replace FK ids with names from summary_fields in every format "
                    "(a table always shows names). Multi-valued FKs (e.g. "
                    "credentials) become lists of names."
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
        """List a complete selection by names, IDs, typed input, or query."""
        if with_scm and not offers_with_scm(spec):
            raise_usage(f"--with-scm is not available for {spec.cli_name}")
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
            if with_scm:
                records = add_scm_fields(
                    records,
                    client=ctx.repo,
                    catalog=ctx.catalog,
                    warn=lambda msg: ctx.progress_ui().message("warning", msg),
                )
        # Default columns shape the table and raw views; json/yaml/pipe keep full records.
        default_cols = (*spec.list_columns, *SCM_FIELDS) if with_scm else spec.list_columns
        shown = default_cols if fmt in {"table", "raw"} else ()
        # Display-only FK columns (e.g. Host's ``inventory``, which lives in
        # ``read_only_fields`` rather than ``fk_refs``) are named too.
        records = name_fks(
            records,
            spec,
            with_names=with_names,
            table=fmt == "table",
            columns=columns,
            defaults=shown,
        )
        records = [redact_value(record, spec.secret_paths, skip_empty=True) for record in records]
        emit(
            records,
            fmt=fmt,
            columns=columns or (list(shown) if fmt == "raw" else None),
            table_columns=default_cols,
            kind=pipe_kind_for_spec(spec),
            empty=False,
        )
