"""``get`` builder for the spec-driven CLI factory.

Also owns ``default_get_columns`` — the public helper shared with
``cli/unified_templates_commands.py`` so the polymorphic browser
projects records the same way as factory-built ``get``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import typer

from untaped import (
    ColumnsOption,
    FormatOption,
    OutputFormat,
    format_output,
    read_identifiers,
    report_errors,
    resolve_each,
)
from untaped_awx.application import GetResource
from untaped_awx.cli._context import open_context, scope_for_command
from untaped_awx.cli._names import flatten_fks
from untaped_awx.infrastructure.spec import AwxResourceSpec


def _add_get(app: typer.Typer, spec: AwxResourceSpec) -> None:
    @app.command("get", no_args_is_help=True)
    def get_command(
        names: list[str] | None = typer.Argument(
            None, help=f"{spec.kind} name(s) or numeric id(s)."
        ),
        stdin: bool = typer.Option(
            False, "--stdin", help="Read names or numeric ids from stdin (one per line)."
        ),
        organization: str | None = typer.Option(
            None, "--organization", help="Scope to organization (ignored for numeric ids)."
        ),
        inventory: str | None = typer.Option(
            None,
            "--inventory",
            help=(
                "Scope to inventory (Host/Group only). Without this, name "
                "lookup is global and ambiguous if the same name exists "
                "across inventories."
            ),
        ),
        inventory_organization: str | None = typer.Option(
            None,
            "--inventory-organization",
            help="Disambiguate same-named inventories across orgs (Host/Group only).",
        ),
        by_name: bool = typer.Option(
            False,
            "--by-name",
            help="Force name lookup (escape hatch for resources whose name is all digits).",
        ),
        with_names: bool = typer.Option(
            False,
            "--with-names",
            help="Replace FK ids with names from summary_fields.",
        ),
        fmt: FormatOption = "yaml",
        columns: ColumnsOption = None,
    ) -> None:
        """Fetch one or more resources by name or numeric id.

        All-digit identifiers are looked up by id, everything else by
        name within the resolved organization scope. Pass ``--by-name``
        to force name lookup when a resource's name happens to be all
        digits.
        """
        records: list[Any] = []
        any_failed = False
        with report_errors(), open_context() as ctx:
            ids = read_identifiers(list(names or []), stdin=stdin)
            scope = scope_for_command(
                ctx,
                organization,
                spec,
                inventory=inventory,
                inventory_organization=inventory_organization,
            )
            getter = GetResource(ctx.repo)
            records, any_failed = resolve_each(
                ids, lambda n: getter.by_identifier(spec, n, scope=scope, by_name=by_name)
            )
        if records:
            cols = list(columns) if columns else default_get_columns(fmt, spec.list_columns)
            if with_names:
                # ``cols`` may be ``None`` for non-table formats — that's
                # fine; ``flatten_fks`` then only flattens declared fk_refs.
                records = flatten_fks(records, spec, columns=cols)
            typer.echo(format_output(records, fmt=fmt, columns=cols))
        if any_failed:
            raise typer.Exit(code=1)


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
