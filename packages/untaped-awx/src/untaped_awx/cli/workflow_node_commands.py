"""``untaped awx workflow-templates nodes`` — list a workflow's contents.

Attaches a sibling ``nodes`` command to the factory-built
``workflow-templates`` sub-app: a read-only inspector that sits outside
:func:`make_resource_app` because the factory's identity-based ``get``
and CRUD assumptions don't apply to a nested sub-collection of a
specific workflow.
"""

from __future__ import annotations

import typer
from untaped_core import (
    ColumnsOption,
    FormatOption,
    UntapedError,
    format_output,
    parse_kv_pairs,
    read_identifiers,
    report_errors,
)

from untaped_awx.application import ListWorkflowNodes
from untaped_awx.cli._context import open_context, scope_for_command
from untaped_awx.domain import WorkflowNode, WorkflowNodeType
from untaped_awx.infrastructure.specs.workflow import WORKFLOW_JOB_TEMPLATE_SPEC

_DEFAULT_COLUMNS = ["id", "name", "type", "depth"]


def register_nodes_command(parent: typer.Typer) -> None:
    """Register the ``nodes`` command on the ``workflow-templates`` sub-app."""

    @parent.command("nodes", no_args_is_help=True)
    def nodes_command(
        identifiers: list[str] | None = typer.Argument(
            None,
            help=(
                "Workflow name(s) or numeric id(s) — one or more, or "
                "omit and pass ``--stdin``. Numeric values skip name "
                "lookup; otherwise each name is resolved against AWX "
                "with the same org-scope rules as ``workflow-templates "
                "get``. Multiple roots concatenate their node trees in "
                "the order given."
            ),
        ),
        stdin: bool = typer.Option(
            False,
            "--stdin",
            help=(
                "Read workflow names or numeric ids from stdin (one per "
                "line); equivalent to passing them positionally. Per-root "
                "failures emit a stderr warning and force a non-zero "
                "exit; other roots still emit their rows."
            ),
        ),
        organization: str | None = typer.Option(
            None,
            "--organization",
            "-o",
            help=(
                "Organization scope for name lookup. Falls back to "
                "``awx.default_organization`` from the active profile."
            ),
        ),
        recursive: bool = typer.Option(
            False,
            "--recursive",
            "-r",
            help=(
                "Expand sub-workflows: every node whose referenced "
                "template is itself a WorkflowJobTemplate is followed "
                "into. Cycle-guarded by workflow id."
            ),
        ),
        depth: int | None = typer.Option(
            None,
            "--depth",
            help=(
                "Cap recursion depth. ``--depth 0`` returns only the "
                "root's nodes. Setting ``--depth N`` for ``N > 0`` "
                "implies ``--recursive``. Unlimited by default when "
                "``--recursive`` is passed alone."
            ),
        ),
        type_: WorkflowNodeType | None = typer.Option(
            None,
            "--type",
            help=(
                "Filter output by template type. Traversal still descends "
                "into every workflow node so a ``--type job_template`` view "
                "with ``--recursive`` surfaces nested job templates."
            ),
        ),
        filter_: list[str] | None = typer.Option(
            None,
            "--filter",
            help=(
                "Server-side filter, KEY=VALUE (repeatable). Passed "
                "verbatim to AWX (e.g. ``--filter unified_job_template"
                "__name__in=t_a,t_b``). With ``--recursive``, applied at "
                "every BFS level — a filter that doesn't match "
                "sub-workflow rows will prune them and stop the descent. "
                "To preserve recursion, OR-in the workflow-job type "
                "(``--filter or__unified_job_template__name__in=t_a,t_b "
                "--filter or__unified_job_template__unified_job_type="
                "workflow_job``) or post-filter on the output."
            ),
        ),
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """List the nodes (contents) of one or more workflow job templates."""
        if depth is not None and depth < 0:
            raise typer.BadParameter("--depth must be non-negative")
        if depth is not None:
            max_depth: int | None = depth
        elif recursive:
            max_depth = None
        else:
            max_depth = 0

        nodes: list[WorkflowNode] = []
        any_failed = False
        with report_errors(), open_context() as ctx:
            roots = read_identifiers(list(identifiers or []), stdin=stdin)
            filters = parse_kv_pairs(filter_, flag="--filter")
            scope = scope_for_command(ctx, organization, WORKFLOW_JOB_TEMPLATE_SPEC)
            use = ListWorkflowNodes(
                ctx.workflow_nodes,
                ctx.repo,
                warn=lambda msg: typer.echo(f"warning: {msg}", err=True),
            )
            # ``resolve_each`` doesn't fit: its ``Callable[[str], R]``
            # interface maps each id to a single record, but ``nodes``
            # produces a ``list[WorkflowNode]`` per root.
            for root in roots:
                try:
                    nodes.extend(
                        use(
                            WORKFLOW_JOB_TEMPLATE_SPEC,
                            identifier=root,
                            scope=scope,
                            max_depth=max_depth,
                            filters=filters,
                        )
                    )
                except UntapedError as exc:
                    typer.echo(f"warning: {root}: {exc}", err=True)
                    any_failed = True
        if type_ is not None:
            nodes = [n for n in nodes if n.type == type_]
        rows = [n.model_dump() for n in nodes]
        cols = list(columns) if columns else list(_DEFAULT_COLUMNS)
        typer.echo(format_output(rows, fmt=fmt, columns=cols))
        if any_failed:
            raise typer.Exit(code=1)
