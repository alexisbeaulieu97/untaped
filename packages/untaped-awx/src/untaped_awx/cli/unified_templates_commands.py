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

app = typer.Typer(
    name="unified-templates",
    help="Browse Unified Job Templates (the polymorphic view of every launchable kind).",
    no_args_is_help=True,
)


@app.callback()
def _callback() -> None:
    """Browse Unified Job Templates."""


_DEFAULT_LIST_COLUMNS = [
    "id",
    "type",
    "name",
    "summary_fields.organization.name",
    # JT/WJT carry ``last_job_status``; Project / InventorySource carry
    # ``status``. Keep both in the default projection — ``format_output``
    # emits empty cells for missing fields so the union is harmless.
    "last_job_status",
    "status",
    "last_job_run",
]


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
        page = ctx.repo.request(
            "GET", "unified_job_templates/", params={**filters, "page_size": "200"}
        )
    records = list(page.get("results") or [])
    if limit is not None:
        records = records[:limit]
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
                # vague 404. Matches ``_get_one`` / ``isdecimal()`` check
                # in ``cli/resource_commands.py``.
                raise typer.BadParameter(
                    f"unified-templates get is id-only ({raw!r} isn't a number); "
                    "names are not unique across kinds — use the per-kind sub-app "
                    "for name lookup.",
                )
            # AWX exposes the collection endpoint only — there's no
            # ``/unified_job_templates/<id>/`` resource URL (UJT is a
            # virtual aggregate). Filter via ``?id=<value>`` on the list
            # endpoint and read the single match. ``page_size=1`` keeps
            # the response tight; AWX returns ``{count, results: []}``
            # so an empty ``results`` cleanly distinguishes a missing
            # id from any other failure.
            page = ctx.repo.request(
                "GET",
                "unified_job_templates/",
                params={"id": raw, "page_size": "1"},
            )
            results = page.get("results") or []
            if results:
                records.append(results[0])
            else:
                typer.echo(f"error: {raw}: not found", err=True)
                any_failed = True
    if records:
        cols = list(columns) if columns else None
        typer.echo(format_output(records, fmt=fmt, columns=cols))
    if any_failed:
        raise typer.Exit(code=1)
