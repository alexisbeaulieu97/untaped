"""``untaped awx workflow-templates nodes``: list a workflow's contents.

A command of the ``workflow-templates`` group: a read-only inspector of a
nested sub-collection of one workflow, outside the identity-based ``get``
and the writes a kind's group derives.
"""

from __future__ import annotations

from collections.abc import Iterator

from pydantic import Field

from untaped.sdk import KeyValue, Output, Positional, Stdin, UntapedError
from untaped_awx.application import ListWorkflowNodes
from untaped_awx.commands._awx import AwxCommand
from untaped_awx.commands._options import OrganizationScope, resolve_max_depth, scope_values
from untaped_awx.commands._template_targets import template_targets
from untaped_awx.commands.context import scope_for_command
from untaped_awx.domain import WorkflowNode, WorkflowNodeType
from untaped_awx.infrastructure.resources.specs.workflow import WORKFLOW_JOB_TEMPLATE_SPEC
from untaped_awx.records import WorkflowJobTemplate

_DEFAULT_COLUMNS = ("id", "name", "type", "depth")


class Nodes(AwxCommand):
    """List the nodes (contents) of one or more workflow job templates.

    With --recursive, nested workflows are expanded. The graph as a document
    you can edit and apply: the `WorkflowNodes` guide of `awx apply`.
    """

    identifiers: Positional[list[str]] = Field(
        default_factory=list,
        description=(
            "Workflow names (one or more), or omit them and pass --stdin. Pass --by-id to "
            "resolve AWX ids instead. Multiple roots concatenate their node trees in the "
            "order given."
        ),
    )
    stdin: Stdin[WorkflowJobTemplate]
    by_id: bool = Field(
        False, description="Look up identifiers as AWX numeric ids instead of names."
    )
    scope: OrganizationScope
    recursive: bool = Field(
        False,
        description=(
            "Expand sub-workflows: every node whose referenced template is itself a "
            "WorkflowJobTemplate is followed into. Cycle-guarded by workflow id."
        ),
    )
    depth: int | None = Field(
        None,
        description=(
            "Cap recursion depth. --depth 0 returns only the root's nodes. Setting --depth N "
            "for N > 0 implies --recursive. Unlimited by default when --recursive is passed "
            "alone."
        ),
    )
    type_: WorkflowNodeType | None = Field(
        None,
        description=(
            "Filter output by template type. Traversal still descends into every workflow "
            "node so a --type job_template view with --recursive surfaces nested job "
            "templates."
        ),
    )
    filter_: list[KeyValue] = Field(
        default_factory=list,
        description="Server-side filter, KEY=VALUE (repeatable). Passed verbatim to AWX.",
    )
    output: Output[WorkflowNode] = Output(columns=_DEFAULT_COLUMNS)

    def run(self) -> Iterator[WorkflowNode]:
        spec = WORKFLOW_JOB_TEMPLATE_SPEC
        max_depth = resolve_max_depth(self.depth, self.recursive)
        self.output.empty("No workflow nodes found.")
        nodes: list[WorkflowNode] = []
        with self.awx() as awx:
            roots = template_targets(self.identifiers, self.stdin, by_id=self.by_id)
            scope = scope_for_command(awx, spec, scope_values(self.scope))
            use = ListWorkflowNodes(awx.workflow_nodes, awx.repo, warn=awx.diag.warning)
            for root, root_by_id in roots:
                try:
                    nodes.extend(
                        use(
                            spec,
                            identifier=root,
                            scope=scope,
                            by_id=root_by_id,
                            max_depth=max_depth,
                            filters=dict(self.filter_),
                        )
                    )
                except UntapedError as exc:
                    awx.diag.failure(exc, item=root)
        yield from (node for node in nodes if self.type_ is None or node.type == self.type_)


__all__ = ["Nodes"]
