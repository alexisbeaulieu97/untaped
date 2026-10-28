"""``untaped awx <templates> usage``: reverse lookup of containing workflows.

A command of the ``job-templates`` and ``workflow-templates`` groups: the
reverse of ``workflow-templates nodes``. Where ``nodes`` answers "what runs
inside this workflow?", ``usage`` answers "which workflows run this
template?", the impact-analysis question to ask before changing or deleting
a template. The group's kind decides which kind the identifier resolves
against; the underlying query is the same either way.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

from pydantic import Field

from untaped.sdk import (
    KeyValue,
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
from untaped_awx.commands._options import OrganizationScope, resolve_max_depth, scope_values
from untaped_awx.commands._template_targets import template_targets
from untaped_awx.commands.context import scope_for_command
from untaped_awx.domain import WorkflowUsage
from untaped_awx.domain.workflow_usage import usage_tree


def _no_tree(rows: object) -> None:
    """``--format tree`` before ``run`` drew its targets: a run that failed draws nothing."""


def _drawing(trees: list[TreeGraph]) -> Callable[[object], list[TreeGraph]]:
    """``--format tree``: one tree per target, a target in no workflow too (it has no row)."""
    return lambda _rows: trees


class Usage(ResourceCommand):
    """List the workflow job templates that contain one or more templates.

    With --recursive, up to the top-level workflows. Each row says what
    the workflow `contains` among the queried template (`target`)
    and the other rows, as `id`, `type` and `node_count` (its
    nodes running it), so the rows of one target hold its whole
    ancestry: `depth` and `node_count` describe the first link, at
    the shortest distance. A workflow reached along several paths is
    one row. `stopped` is `depth` when the depth limit stopped the
    walk before the workflow's own containers were looked up (without
    --recursive or --depth, every row), so a row with `stopped: null`
    that no row contains is a top-level workflow. --filter applies to
    every lookup, so a workflow whose containers it filters out looks
    top-level too. A workflow template in its own ancestry (a cycle) is
    a row of its own usage, at the depth the walk met it, and each
    cycle is warned about on stderr.

    --format tree draws the same rows: one tree per target, each
    workflow under every template it runs, highlighted (the theme's
    success color, bold green by default) and listed in the summary
    when it is top-level (not with --filter), `(N nodes)` after a
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
    by_id: bool = Field(
        False, description="Look up identifiers as AWX numeric ids instead of names."
    )
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
    filter_: list[KeyValue] = Field(
        default_factory=list,
        description="Server-side filter, KEY=VALUE (repeatable). Passed verbatim to AWX.",
    )
    output: Output[WorkflowUsage] = Output(render={"tree": _no_tree})

    def run(self) -> Iterator[WorkflowUsage]:
        max_depth = resolve_max_depth(self.depth, self.recursive)
        self.output.empty("No containing workflows found.")
        diag = self.ctx.diag
        rows: list[WorkflowUsage] = []
        trees: list[TreeGraph] = []
        # the tree draws every target, which the rows alone can't: a target in no workflow has none
        self.output.render = {**self.output.render, "tree": _drawing(trees)}
        top_level = not self.filter_
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
                tree = usage_tree(answer.target, answer.rows, top_level=top_level)
                for edge in tree.cycle_edges():
                    diag.warning(
                        f"workflow {edge.child} runs itself (a cycle)"
                        if edge.child == edge.parent
                        else f"workflow {edge.child} runs workflow {edge.parent}, "
                        "which already runs it (a cycle)"
                    )
                trees.append(tree)
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
