"""``untaped awx <templates> usage``: reverse lookup of containing workflows.

A command of the ``job-templates`` and ``workflow-templates`` groups: the
reverse of ``workflow-templates nodes``. Where ``nodes`` answers "what runs
inside this workflow?", ``usage`` answers "which workflows run this
template?", the impact-analysis question to ask before changing or deleting
a template. The group's kind decides which kind the identifier resolves
against; the underlying query is the same either way.
"""

from __future__ import annotations

from collections.abc import Iterator

from pydantic import Field

from untaped.sdk import (
    Output,
    Positional,
    Record,
    Stdin,
    TreeGraph,
    UntapedError,
    plural,
)
from untaped_awx.application import ListTemplateUsage
from untaped_awx.commands._awx import ResourceCommand
from untaped_awx.commands._options import (
    ById,
    Filters,
    OrganizationScope,
    resolve_max_depth,
    scope_values,
)
from untaped_awx.commands._template_targets import template_targets
from untaped_awx.commands.context import scope_for_command
from untaped_awx.domain import WorkflowUsage
from untaped_awx.domain.workflow_usage import usage_tree


def _trees(rows: WorkflowUsage | list[WorkflowUsage]) -> list[TreeGraph]:
    """``--format tree``: one tree per target given, its rows drawn under it.

    A target's rows come together, and a target given twice comes twice: a
    new tree starts at another target, or at a workflow the tree already has.
    """
    groups: list[list[WorkflowUsage]] = []
    for row in [rows] if isinstance(rows, WorkflowUsage) else rows:
        group = groups[-1] if groups else []
        if not group or group[0].target != row.target or row.id in {r.id for r in group}:
            groups.append(group := [])
        group.append(row)
    return [usage_tree(group[0].target, group) for group in groups]


class Usage(ResourceCommand):
    """List the workflow job templates that contain one or more templates.

    With --recursive, up to the top-level workflows. Each row names the
    queried template (`target`: its `id`, `name` and `type`) and says
    what the workflow `contains` among it and the other rows, as `id`,
    `type` and `node_count` (its nodes running it), so the rows of one
    target hold its whole ancestry: `depth` and `node_count` describe
    the first link, at the shortest distance. A workflow reached along
    several paths is one row. `stopped` is `depth` when the depth limit
    stopped the walk before the workflow's own containers were looked
    up (without --recursive or --depth, every row). `top_level` is true
    for a workflow no workflow runs; never with --filter, which applies
    to every lookup and can hide a workflow's containers. A workflow
    template in its own ancestry (a cycle) is a row of its own usage, at
    the depth the walk met it, and each cycle is warned about on stderr; a
    target in no workflow is said there too (not under --quiet).

    --format tree draws the same rows: one tree per target, each
    workflow under every template it runs, highlighted (the theme's
    success color, bold green by default) and listed in the summary
    when it is top-level, `(N nodes)` after a
    workflow running the line above in more than one node, and `…`
    where the depth limit stopped (a stderr hint says how to read
    further). A branch drawn earlier is numbered `[n]` and later holds
    one `… see [n]` line; a cycle is marked `↻ cycle`. Set
    `untaped.ui.border: ascii` for a terminal that misdraws box characters.
    """

    identifiers: Positional[list[str]] = Field(
        default_factory=list,
        description=(
            "Template names (one or more), or omit them and pass --stdin. Pass --by-id to "
            "resolve AWX ids instead. Multiple targets concatenate their usage rows in the "
            "order given (dedup is per target)."
        ),
    )
    stdin: Stdin[Record]
    by_id: ById
    scope: OrganizationScope
    recursive: bool = Field(
        False,
        description=(
            "Walk up the ancestry: every workflow that contains the template is itself "
            "looked up in turn, surfacing grandparent workflows. Cycle-guarded by workflow id."
        ),
    )
    depth: int | None = Field(
        None,
        description=(
            "Cap the ancestry walk. --depth 0 returns only direct parents. Setting --depth N "
            "for N > 0 implies --recursive. Unlimited by default when --recursive is passed "
            "alone."
        ),
    )
    filter_: Filters
    output: Output[WorkflowUsage] = Output(render={"tree": _trees})

    def run(self) -> Iterator[WorkflowUsage]:
        max_depth = resolve_max_depth(self.depth, self.recursive)
        self.output.empty("No containing workflows found.")
        diag = self.ctx.diag
        rows: list[WorkflowUsage] = []
        with self.awx() as awx:
            targets = template_targets(self.identifiers, self.stdin, by_id=self.by_id)
            scope = scope_for_command(awx, self.spec, scope_values(self.scope))
            use = ListTemplateUsage(awx.workflow_nodes, awx.repo)
            for target, target_by_id in targets:
                try:
                    answer = use(
                        self.spec,
                        identifier=target,
                        scope=scope,
                        by_id=target_by_id,
                        max_depth=max_depth,
                        filters=dict(self.filter_),
                    )
                except UntapedError as exc:
                    diag.failure(exc, item=target)
                    continue
                if not answer.rows:
                    diag.info(f"{answer.target.name or target} runs in no workflow")
                for edge in usage_tree(answer.target, answer.rows).cycle_edges():
                    diag.warning(
                        f"workflow {edge.child} runs itself (a cycle)"
                        if edge.child == edge.parent
                        else f"workflow {edge.child} runs workflow {edge.parent}, "
                        "which already runs it (a cycle)"
                    )
                rows.extend(answer.rows)
        if self.output.format == "tree" and (
            stopped := sum(row.stopped is not None for row in rows)
        ):
            diag.hint(
                f"pass --recursive to walk up to the top-level workflows ({stopped} stopped)"
                if self.depth is None
                else f"{plural(stopped, 'workflow')} not read beyond --depth {self.depth}; "
                "raise --depth to read them"
            )
        yield from rows


__all__ = ["Usage"]
