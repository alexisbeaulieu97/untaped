"""``list`` builder for the spec-driven CLI factory."""

from typing import Annotated, Any

from cyclopts import App, Parameter

from untaped.api import (
    ColumnsOption,
    FormatOption,
    emit,
    finish,
    parse_kv_pairs,
    raise_usage,
    read_identifiers,
    report_errors,
    resolve_each,
)
from untaped.capabilities.awx.application import GetResource, ListResources
from untaped.capabilities.awx.cli._context import open_context, scope_for_command
from untaped.capabilities.awx.cli._names import flatten_fks
from untaped.capabilities.awx.cli._pipe import id_field_for, pipe_kind_for_spec
from untaped.capabilities.awx.cli.options import (
    ByIdOption,
    InventoryOrganizationOption,
    InventoryStdinLookupOption,
    OrganizationStdinLookupOption,
)
from untaped.capabilities.awx.infrastructure.spec import AwxResourceSpec


def _add_list(app: App, spec: AwxResourceSpec) -> None:
    @app.command(name="list")
    def list_command(
        *,
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
        stdin: Annotated[
            bool,
            Parameter(
                name="--stdin",
                negative="",
                help="Read names from stdin (one per line); render only those records.",
            ),
        ] = False,
        by_id: ByIdOption = False,
        organization: OrganizationStdinLookupOption = None,
        inventory: InventoryStdinLookupOption = None,
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
        """List resources, optionally restricted to identifiers from stdin.

        With ``--stdin``, reads newline-separated names (or ids when
        ``--by-id`` is passed) and renders only those records — same
        identifier semantics as ``get --stdin`` but with the tabular
        columns view ``list`` uses. Cannot be combined with
        ``--search``/``--filter``/``--limit``. The
        ``--organization`` / ``--org`` / ``--inventory`` /
        ``--inventory-organization`` / ``--inventory-org``
        scope flags apply to ``--stdin`` name lookups only (they have no
        effect on server-side filtering, which already accepts
        ``--filter organization__name=…``).
        """
        if stdin and (search or filter_ or limit is not None):
            raise_usage("--stdin cannot be combined with --search/--filter/--limit")
        records: list[dict[str, Any]] = []
        any_failed = False
        with report_errors(), open_context() as ctx:
            if stdin:
                ids = read_identifiers([], stdin=True, id_field=id_field_for(spec, by_id=by_id))
                scope = scope_for_command(
                    ctx,
                    organization,
                    spec,
                    inventory=inventory,
                    inventory_organization=inventory_organization,
                )
                getter = GetResource(ctx.repo)
                records, any_failed = resolve_each(
                    ids, lambda n: getter.by_identifier(spec, n, scope=scope, by_id=by_id)
                )
            else:
                filters = parse_kv_pairs(filter_, flag="--filter")
                with ctx.progress_ui().progress(f"Loading {spec.kind}…"):
                    records = list(
                        ListResources(ctx.repo)(spec, search=search, filters=filters, limit=limit)
                    )
        cols = list(columns) if columns else list(spec.list_columns)
        if with_names:
            # Pass ``cols`` so display-only FK columns (e.g. Host's
            # ``inventory``, which lives in ``read_only_fields`` rather
            # than ``fk_refs``) get flattened from ``summary_fields``.
            records = flatten_fks(records, spec, columns=cols)
        # In ``--stdin`` mode every input identifier already reported its
        # own ``error:`` line; an all-failed batch leaves ``records``
        # empty and we skip the redundant ``[]`` to keep stdout clean for
        # piping. In normal mode an empty list still renders (``[]`` for
        # json/yaml, header-only table, blank for raw) so downstream
        # tools like ``jq`` always see a valid document.
        if records or not stdin:
            emit(
                records,
                fmt=fmt,
                columns=cols,
                kind=pipe_kind_for_spec(spec),
                empty=f"No matching {spec.kind} found. Try a different --search or --filter.",
            )
        finish(any_failed)
