"""``untaped awx workflow-templates nodes`` — list a workflow's contents.

Attaches a sibling ``nodes`` command to the factory-built
``workflow-templates`` sub-app. Mirrors the ``unified_templates_commands``
precedent: a read-only inspector that sits outside
:func:`make_resource_app` because the factory's identity-based ``get``
and CRUD assumptions don't apply to a nested sub-collection of a
specific workflow.

Recursion is opt-in via ``--recursive`` (or implicitly enabled by
``--depth N`` for ``N > 0``). The traversal is cycle-guarded inside the
use case; on cycle, a warning is emitted to stderr and the offending
re-entry is skipped.
"""

from __future__ import annotations

import typer
from untaped_core import (
    ColumnsOption,
    FormatOption,
    format_output,
    report_errors,
)

from untaped_awx.application import ListWorkflowNodes
from untaped_awx.cli._context import open_context, scope_for_spec
from untaped_awx.infrastructure.specs.workflow import WORKFLOW_JOB_TEMPLATE_SPEC

_DEFAULT_COLUMNS = ["id", "name", "type", "depth"]


def register_nodes_command(parent: typer.Typer) -> None:
    """Register the ``nodes`` command on the ``workflow-templates`` sub-app."""

    @parent.command("nodes", no_args_is_help=True)
    def nodes_command(
        identifier: str = typer.Argument(
            ...,
            help=(
                "Workflow name OR numeric id. Numeric values skip name "
                "lookup; otherwise the name is resolved against AWX with "
                "the same org-scope rules as ``workflow-templates get``."
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
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """List the nodes (contents) of a workflow job template."""
        if depth is not None and depth < 0:
            raise typer.BadParameter("--depth must be non-negative")
        do_recurse = recursive or (depth is not None and depth > 0)
        max_depth = depth if do_recurse else 0

        with report_errors(), open_context() as ctx:
            scope = scope_for_spec(
                WORKFLOW_JOB_TEMPLATE_SPEC,
                organization=organization,
                default_organization=ctx.default_organization,
            )
            use = ListWorkflowNodes(
                ctx.workflow_nodes,
                ctx.repo,
                warn=lambda msg: typer.echo(f"warning: {msg}", err=True),
            )
            nodes = use(
                WORKFLOW_JOB_TEMPLATE_SPEC,
                identifier=identifier,
                scope=scope,
                recursive=do_recurse,
                max_depth=max_depth,
            )
        rows = [n.model_dump() for n in nodes]
        cols = list(columns) if columns else list(_DEFAULT_COLUMNS)
        typer.echo(format_output(rows, fmt=fmt, columns=cols))
