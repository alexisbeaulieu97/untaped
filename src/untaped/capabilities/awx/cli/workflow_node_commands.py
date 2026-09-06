"""``untaped awx workflow-templates nodes`` — list a workflow's contents.

Attaches a sibling ``nodes`` command to the factory-built
``workflow-templates`` sub-app: a read-only inspector that sits outside
:func:`make_resource_app` because the factory's identity-based ``get``
and CRUD assumptions don't apply to a nested sub-collection of a
specific workflow.
"""

from typing import Annotated

from cyclopts import App, Parameter

from untaped.api import (
    ColumnsOption,
    FormatOption,
    UntapedError,
    echo,
    emit,
    finish,
    parse_kv_pairs,
    read_identifiers,
    report_errors,
)
from untaped.capabilities.awx.application import ListWorkflowNodes
from untaped.capabilities.awx.cli._context import open_context, scope_for_command
from untaped.capabilities.awx.cli._pipe import id_field_for
from untaped.capabilities.awx.cli.options import ByIdOption, OrganizationOption, resolve_max_depth
from untaped.capabilities.awx.domain import WorkflowNode, WorkflowNodeType
from untaped.capabilities.awx.infrastructure.specs.workflow import WORKFLOW_JOB_TEMPLATE_SPEC

_DEFAULT_COLUMNS = ["id", "name", "type", "depth"]


def register_nodes_command(parent: App) -> None:
    """Register the ``nodes`` command on the ``workflow-templates`` sub-app."""

    @parent.command(name="nodes")
    def nodes_command(
        identifiers: Annotated[
            list[str] | None,
            Parameter(
                help=(
                    "Workflow name(s) — one or more, or omit and pass "
                    "``--stdin``. Pass ``--by-id`` to resolve AWX ids "
                    "instead. Multiple roots concatenate their node trees "
                    "in the order given."
                ),
            ),
        ] = None,
        *,
        stdin: Annotated[
            bool,
            Parameter(
                name="--stdin",
                negative="",
                help=(
                    "Read workflow names from stdin (one per line); "
                    "equivalent to passing them positionally. Per-root "
                    "failures emit a stderr warning and force a non-zero "
                    "exit; other roots still emit their rows."
                ),
            ),
        ] = False,
        by_id: ByIdOption = False,
        organization: OrganizationOption = None,
        recursive: Annotated[
            bool,
            Parameter(
                name=["--recursive", "-r"],
                negative="",
                help=(
                    "Expand sub-workflows: every node whose referenced "
                    "template is itself a WorkflowJobTemplate is followed "
                    "into. Cycle-guarded by workflow id."
                ),
            ),
        ] = False,
        depth: Annotated[
            int | None,
            Parameter(
                name="--depth",
                help=(
                    "Cap recursion depth. ``--depth 0`` returns only the "
                    "root's nodes. Setting ``--depth N`` for ``N > 0`` "
                    "implies ``--recursive``. Unlimited by default when "
                    "``--recursive`` is passed alone."
                ),
            ),
        ] = None,
        type_: Annotated[
            WorkflowNodeType | None,
            Parameter(
                name="--type",
                help=(
                    "Filter output by template type. Traversal still descends "
                    "into every workflow node so a ``--type job_template`` view "
                    "with ``--recursive`` surfaces nested job templates."
                ),
            ),
        ] = None,
        filter_: Annotated[
            list[str] | None,
            Parameter(
                name="--filter",
                help="Server-side filter, KEY=VALUE (repeatable). Passed verbatim to AWX.",
                consume_multiple=False,
            ),
        ] = None,
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """List the nodes (contents) of one or more workflow job templates."""
        max_depth = resolve_max_depth(depth, recursive)

        nodes: list[WorkflowNode] = []
        any_failed = False
        with report_errors(), open_context() as ctx:
            roots = read_identifiers(
                list(identifiers or []),
                stdin=stdin,
                id_field=id_field_for(WORKFLOW_JOB_TEMPLATE_SPEC, by_id=by_id),
            )
            filters = parse_kv_pairs(filter_, flag="--filter")
            scope = scope_for_command(ctx, organization, WORKFLOW_JOB_TEMPLATE_SPEC)
            use = ListWorkflowNodes(
                ctx.workflow_nodes,
                ctx.repo,
                warn=lambda msg: echo(f"warning: {msg}", err=True),
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
                            by_id=by_id,
                            max_depth=max_depth,
                            filters=filters,
                        )
                    )
                except UntapedError as exc:
                    echo(f"warning: {root}: {exc}", err=True)
                    any_failed = True
        if type_ is not None:
            nodes = [n for n in nodes if n.type == type_]
        rows = [n.model_dump() for n in nodes]
        cols = list(columns) if columns else list(_DEFAULT_COLUMNS)
        emit(rows, fmt=fmt, columns=cols, kind="awx.workflow_node")
        finish(any_failed)
