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
  ``projects sync``, …); polymorphic launch dispatch isn't worth the
  complexity when the per-kind path is already complete.

Implementation follows the ``jobs_app`` precedent in
``cli/commands.py`` rather than ``make_resource_app``: the resource-app
factory bakes in CRUD assumptions and identity-based ``get`` that this
virtual collection cannot satisfy.
"""

from typing import Annotated

from cyclopts import Parameter

from untaped.api import (
    ColumnsOption,
    FormatOption,
    OutputFormat,
    create_app,
    echo,
    emit,
    finish,
    parse_kv_pairs,
    raise_usage,
    read_identifiers,
    render_rows,
    report_errors,
)
from untaped.capabilities.awx.application import BrowseUnifiedTemplates, GetUnifiedTemplate
from untaped.capabilities.awx.cli._context import open_context
from untaped.capabilities.awx.cli._get import default_get_columns

app = create_app(
    name="unified-templates",
    help="Browse Unified Job Templates (the polymorphic view of every launchable kind).",
)


_DEFAULT_LIST_COLUMNS = ["id", "name", "type"]
"""Strict-minimal projection: identity + the polymorphic discriminator.

Kept tight on purpose — the four kinds carry different health fields
(JT/WJT use ``last_job_status``, Project/InventorySource use ``status``)
so any health column is empty for half the rows. Users who want health
or organization context project them explicitly via ``--columns``."""


@app.command(name="list")
def list_command(
    *,
    type_: Annotated[
        str | None,
        Parameter(
            name="--type",
            help=(
                "Filter by AWX type discriminator. Common values: "
                "job_template, workflow_job_template, project, inventory_source."
            ),
        ),
    ] = None,
    filter_: Annotated[
        list[str] | None,
        Parameter(
            name="--filter",
            help="Server-side filter, KEY=VALUE (repeatable). Forwarded verbatim to AWX.",
            consume_multiple=False,
        ),
    ] = None,
    limit: Annotated[int | None, Parameter(name="--limit", help="Cap result count.")] = None,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """List Unified Job Templates (alphabetical by name)."""
    filters = parse_kv_pairs(filter_, flag="--filter")
    if type_ is not None:
        if "type" in filters:
            raise_usage(
                "pass --type or --filter type=…, not both — they collide on the same param",
            )
        filters["type"] = type_
    with report_errors(), open_context() as ctx, ctx.progress_ui().progress("Loading templates…"):
        records = list(BrowseUnifiedTemplates(ctx.ujts)(params=filters, limit=limit))
    cols = list(columns) if columns else list(_DEFAULT_LIST_COLUMNS)
    rendered = render_rows(
        records,
        fmt=fmt,
        columns=cols,
        kind="awx.unified_template",
        empty="No templates found. Try a different --type or --filter.",
    )
    if rendered:
        echo(rendered)


@app.command(name="get")
def get_command(
    ids: Annotated[
        list[str] | None,
        Parameter(
            help=(
                "Numeric Unified Job Template id(s). Names are not unique across kinds — "
                "use the per-kind sub-app for name lookup."
            ),
        ),
    ] = None,
    *,
    stdin: Annotated[
        bool,
        Parameter(name="--stdin", negative="", help="Read numeric ids from stdin (one per line)."),
    ] = False,
    fmt: Annotated[OutputFormat, Parameter(name=["--format", "-f"])] = "yaml",
    columns: ColumnsOption = None,
) -> None:
    """Fetch one or more Unified Job Templates by numeric id."""
    records: list[dict[str, object]] = []
    missing: list[str] = []
    with report_errors(), open_context() as ctx:
        identifiers = read_identifiers(list(ids or []), stdin=stdin, id_field="id")
        for raw in identifiers:
            if not raw.isdecimal():
                # Fast-fail before hitting AWX so the error message is
                # specifically about the id-only contract instead of a
                # vague 404.
                raise_usage(
                    f"unified-templates get is id-only ({raw!r} isn't a number); "
                    "names are not unique across kinds — use the per-kind sub-app "
                    "for name lookup.",
                )
        if not identifiers:
            return
        records, missing = GetUnifiedTemplate(ctx.ujts)(ids=identifiers)
    for raw in missing:
        echo(f"error: {raw}: not found", err=True)
    if records:
        cols = list(columns) if columns else default_get_columns(fmt, _DEFAULT_LIST_COLUMNS)
        emit(records, fmt=fmt, columns=cols, kind="awx.unified_template")
    finish(bool(missing))
