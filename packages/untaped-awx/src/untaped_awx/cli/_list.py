"""``list`` builder for the spec-driven CLI factory."""

from __future__ import annotations

from typing import Any

import typer
from untaped_core import (
    ColumnsOption,
    FormatOption,
    format_output,
    parse_kv_pairs,
    read_identifiers,
    report_errors,
    resolve_each,
)

from untaped_awx.application import GetResource, ListResources
from untaped_awx.cli._context import _scope, open_context
from untaped_awx.cli._names import flatten_fks
from untaped_awx.infrastructure.spec import AwxResourceSpec


def _add_list(app: typer.Typer, spec: AwxResourceSpec) -> None:
    @app.command("list")
    def list_command(
        search: str | None = typer.Option(None, "--search", help="Fuzzy server-side search."),
        filter_: list[str] | None = typer.Option(
            None,
            "--filter",
            help=(
                "Server-side filter, KEY=VALUE (repeatable). Passed verbatim to "
                "AWX, so any Django-style lookup works: --filter "
                "organization__name=Default --filter name__icontains=deploy."
            ),
        ),
        limit: int | None = typer.Option(None, "--limit", help="Cap result count."),
        stdin: bool = typer.Option(
            False,
            "--stdin",
            help="Read names or numeric ids from stdin (one per line); render only those records.",
        ),
        organization: str | None = typer.Option(
            None,
            "--organization",
            help="Scope ``--stdin`` name lookups to an organization (ignored for numeric ids).",
        ),
        inventory: str | None = typer.Option(
            None,
            "--inventory",
            help="Scope ``--stdin`` name lookups to an inventory (Host/Group only).",
        ),
        inventory_organization: str | None = typer.Option(
            None,
            "--inventory-organization",
            help="Disambiguate same-named inventories across orgs (Host/Group only).",
        ),
        with_names: bool = typer.Option(
            False,
            "--with-names",
            help=(
                "Replace FK ids with names from summary_fields. Multi-valued "
                "FKs (e.g. credentials) become lists of names."
            ),
        ),
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """List resources, optionally restricted to names/ids from stdin.

        With ``--stdin``, reads newline-separated names or numeric ids and
        renders only those records — same identifier semantics as ``get
        --stdin`` but with the tabular columns view ``list`` uses. Cannot
        be combined with ``--search``/``--filter``/``--limit``. The
        ``--organization`` / ``--inventory`` / ``--inventory-organization``
        scope flags apply to ``--stdin`` name lookups only (they have no
        effect on server-side filtering, which already accepts
        ``--filter organization__name=…``).
        """
        if stdin and (search or filter_ or limit is not None):
            raise typer.BadParameter("--stdin cannot be combined with --search/--filter/--limit")
        records: list[dict[str, Any]] = []
        any_failed = False
        with report_errors(), open_context() as ctx:
            if stdin:
                ids = read_identifiers([], stdin=True)
                scope = _scope(
                    ctx,
                    organization,
                    spec,
                    inventory=inventory,
                    inventory_organization=inventory_organization,
                )
                getter = GetResource(ctx.repo)
                records, any_failed = resolve_each(
                    ids, lambda n: getter.by_identifier(spec, n, scope=scope)
                )
            else:
                filters = parse_kv_pairs(filter_, flag="--filter")
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
            typer.echo(format_output(records, fmt=fmt, columns=cols))
        if any_failed:
            raise typer.Exit(code=1)
