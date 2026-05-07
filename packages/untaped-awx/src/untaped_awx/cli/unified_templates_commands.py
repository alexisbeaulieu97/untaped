"""``untaped awx unified-templates`` — polymorphic templates browser.

Read-only surface over AWX's ``/api/v2/unified_job_templates/`` virtual
collection, which aggregates ``JobTemplate``, ``WorkflowJobTemplate``,
``Project``, and ``InventorySource`` rows behind a single ``type``
discriminator string. The AWX UI's "Templates" page shows the same view.

Browse-only by design:

- Names are not unique across kinds (a JobTemplate and a Project can
  both be called ``deploy``), so ``get`` is **id-only** — name lookup
  would silently pick the wrong row. For name lookup, drop into the
  per-kind sub-app (``job-templates get deploy``, etc.).
- Launching stays on the per-kind sub-apps (``job-templates launch``,
  ``projects update``, …); polymorphic launch dispatch isn't worth the
  complexity when the per-kind path is already complete.

Implementation follows the ``jobs_app`` precedent in
``cli/commands.py`` rather than ``make_resource_app``: the resource-app
factory bakes in CRUD assumptions and identity-based ``get`` that this
virtual collection cannot satisfy.
"""

from __future__ import annotations

import typer
from untaped_core import (
    ColumnsOption,
    FormatOption,
    OutputFormat,
    format_output,
    parse_kv_pairs,
    read_identifiers,
    report_errors,
)

from untaped_awx.cli._context import open_context
from untaped_awx.cli.resource_commands import default_get_columns

app = typer.Typer(
    name="unified-templates",
    help="Browse Unified Job Templates (the polymorphic view of every launchable kind).",
    no_args_is_help=True,
)


@app.callback()
def _callback() -> None:
    """Browse Unified Job Templates."""


_DEFAULT_LIST_COLUMNS = ["id", "name", "type"]
"""Strict-minimal projection: identity + the polymorphic discriminator.

Kept tight on purpose — the four kinds carry different health fields
(JT/WJT use ``last_job_status``, Project/InventorySource use ``status``)
so any health column is empty for half the rows. Users who want health
or organization context project them explicitly via ``--columns``."""


@app.command("list")
def list_command(
    type_: str | None = typer.Option(
        None,
        "--type",
        help=(
            "Filter by AWX type discriminator. Common values: "
            "job_template, workflow_job_template, project, inventory_source. "
            "Forwarded verbatim, so any value AWX accepts works."
        ),
    ),
    filter_: list[str] | None = typer.Option(
        None,
        "--filter",
        help=(
            "Server-side filter, KEY=VALUE (repeatable). Forwarded verbatim "
            "to AWX so any Django-style lookup applies (--filter "
            "name__icontains=deploy, --filter organization__name=Default, …)."
        ),
    ),
    limit: int | None = typer.Option(None, "--limit", help="Cap result count."),
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """List Unified Job Templates (alphabetical by name)."""
    filters = parse_kv_pairs(filter_, flag="--filter")
    if type_ is not None:
        if "type" in filters:
            raise typer.BadParameter(
                "pass --type or --filter type=…, not both — they collide on the same param",
            )
        filters["type"] = type_
    # Alphabetical ordering generalises across kinds; ``-id`` would
    # interleave creation timelines from four different tables.
    filters.setdefault("order_by", "name")
    with report_errors(), open_context() as ctx:
        records = list(
            ctx.repo.paginate_path("unified_job_templates/", params=filters, limit=limit)
        )
    cols = list(columns) if columns else list(_DEFAULT_LIST_COLUMNS)
    typer.echo(format_output(records, fmt=fmt, columns=cols))


@app.command("get", no_args_is_help=True)
def get_command(
    ids: list[str] | None = typer.Argument(
        None,
        help=(
            "Numeric Unified Job Template id(s). Names are not unique across kinds — "
            "use the per-kind sub-app (job-templates get, projects get, …) for name lookup."
        ),
    ),
    stdin: bool = typer.Option(
        False, "--stdin", help="Read numeric ids from stdin (one per line)."
    ),
    fmt: OutputFormat = typer.Option("yaml", "--format", "-f"),
    columns: ColumnsOption = None,
) -> None:
    """Fetch one or more Unified Job Templates by numeric id."""
    records: list[dict[str, object]] = []
    any_failed = False
    with report_errors(), open_context() as ctx:
        identifiers = read_identifiers(list(ids or []), stdin=stdin)
        for raw in identifiers:
            if not raw.isdecimal():
                # Fast-fail before hitting AWX so the error message is
                # specifically about the id-only contract instead of a
                # vague 404.
                raise typer.BadParameter(
                    f"unified-templates get is id-only ({raw!r} isn't a number); "
                    "names are not unique across kinds — use the per-kind sub-app "
                    "for name lookup.",
                )
        if not identifiers:
            return
        # AWX exposes the collection endpoint only — there's no
        # ``/unified_job_templates/<id>/`` resource URL (UJT is a
        # virtual aggregate). Use ``?id__in=…`` so a multi-id batch is
        # one round trip; the list endpoint paginates so we walk pages
        # in case ``len(ids) > page_size``.
        id_in = ",".join(identifiers)
        records = list(
            ctx.repo.paginate_path(
                "unified_job_templates/",
                params={"id__in": id_in, "order_by": "id"},
            )
        )
        # Per-id error reporting: ids the bulk fetch didn't return are
        # missing. Cast to int when possible since AWX returns numeric
        # ids; fall back to string compare for safety.
        found_ids = {str(r.get("id")) for r in records}
        for raw in identifiers:
            if raw not in found_ids:
                typer.echo(f"error: {raw}: not found", err=True)
                any_failed = True
    if records:
        cols = list(columns) if columns else default_get_columns(fmt, _DEFAULT_LIST_COLUMNS)
        typer.echo(format_output(records, fmt=fmt, columns=cols))
    if any_failed:
        raise typer.Exit(code=1)
