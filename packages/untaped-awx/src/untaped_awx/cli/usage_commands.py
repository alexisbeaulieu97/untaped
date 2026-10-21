"""``untaped awx <templates> usage`` — reverse lookup of containing workflows.

Attaches a sibling ``usage`` command to the factory-built
``job-templates`` and ``workflow-templates`` sub-apps: the reverse of
``workflow-templates nodes``. Where ``nodes`` answers "what runs inside
this workflow?", ``usage`` answers "which workflows run this template?"
— the impact-analysis question to ask before changing or deleting a
template. It sits outside :func:`make_resource_app` because the
factory's identity-based ``get`` and CRUD assumptions don't apply to a
derived view over the workflow-node collection. The parent sub-app's
spec decides which kind the identifier resolves against; the underlying
query is the same either way.
"""

from __future__ import annotations

from typing import Annotated

from cyclopts import App, Parameter

from untaped.sdk import (
    ColumnsOption,
    Diagnostics,
    TreeFormatOption,
    UntapedError,
    check_columns,
    emit,
    finish,
    parse_kv_pairs,
    plural,
    report_errors,
)
from untaped_awx.application import ListTemplateUsage
from untaped_awx.cli.context import open_context, scope_for_command
from untaped_awx.cli.options import ByIdOption, OrganizationOption, resolve_max_depth
from untaped_awx.cli.template_targets import template_targets
from untaped_awx.domain import WorkflowUsage
from untaped_awx.domain.workflow_usage import UsageAnswer, usage_tree
from untaped_awx.infrastructure.resources.spec import AwxResourceSpec

_RAW_COLUMNS = list(WorkflowUsage.table_columns)
"""``raw`` prints the table's default columns."""


def register_usage_command(parent: App, spec: AwxResourceSpec) -> None:
    """Register the ``usage`` command on a template sub-app."""

    @parent.command(name="usage")
    def usage_command(
        identifiers: Annotated[
            list[str] | None,
            Parameter(
                help=(
                    "Template names (one or more), or omit them and pass "
                    "``--stdin``. Pass ``--by-id`` to resolve AWX ids "
                    "instead. Multiple targets concatenate their usage "
                    "rows in the order given (dedup is per target)."
                ),
            ),
        ] = None,
        /,
        *,
        stdin: Annotated[
            bool,
            Parameter(
                name="--stdin",
                negative="",
                help=(
                    "Read template names from stdin (one per line), or "
                    "``--format pipe`` records of the template's kind, which "
                    "are looked up by their ``id``. Per-target "
                    "failures emit a stderr warning and force a non-zero "
                    "exit; other targets still emit their rows."
                ),
            ),
        ] = False,
        by_id: ByIdOption = False,
        organization: OrganizationOption = None,
        recursive: Annotated[
            bool,
            Parameter(
                name="--recursive",
                negative="",
                help=(
                    "Walk up the ancestry: every workflow that contains "
                    "the template is itself looked up in turn, surfacing "
                    "grandparent workflows. Cycle-guarded by workflow id."
                ),
            ),
        ] = False,
        depth: Annotated[
            int | None,
            Parameter(
                name="--depth",
                help=(
                    "Cap the ancestry walk. ``--depth 0`` returns only "
                    "direct parents. Setting ``--depth N`` for ``N > 0`` "
                    "implies ``--recursive``. Unlimited by default when "
                    "``--recursive`` is passed alone."
                ),
            ),
        ] = None,
        filter_: Annotated[
            list[str] | None,
            Parameter(
                name="--filter",
                help="Server-side filter, KEY=VALUE (repeatable). Passed verbatim to AWX.",
                consume_multiple=False,
                negative="",
            ),
        ] = None,
        fmt: TreeFormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """List the workflow job templates that contain one or more templates.

        With --recursive, up to the top-level workflows. Each row says what
        the workflow ``contains`` among the queried template (``target``)
        and the other rows, as ``id``, ``type`` and ``node_count`` (its
        nodes running it), so the rows of one target hold its whole
        ancestry: ``depth`` and ``node_count`` describe the first link, at
        the shortest distance. A workflow reached along several paths is
        one row. ``stopped`` is ``depth`` when the depth limit stopped the
        walk before the workflow's own containers were looked up (without
        --recursive or --depth, every row), so a row with ``stopped: null``
        that no row contains is a top-level workflow. --filter applies to
        every lookup, so a workflow whose containers it filters out looks
        top-level too. A workflow template in its own ancestry (a cycle) is
        a row of its own usage, at the depth the walk met it, and each
        cycle is warned about on stderr.

        --format tree draws the same rows: one tree per target, each
        workflow under every template it runs, highlighted (the theme's
        success color, bold green by default) and listed in the summary
        when it is top-level (not with --filter), ``(N nodes)`` after a
        workflow running the line above in more than one node, and ``…`` where
        the depth limit stopped (a stderr hint says how to read further). A
        branch drawn earlier is numbered ``[n]`` and later holds one
        ``… see [n]`` line; a cycle is marked ``↻ cycle``. Set
        ``ui.border: ascii`` for a terminal that misdraws box characters.
        """
        max_depth = resolve_max_depth(depth, recursive)
        check_columns(WorkflowUsage, columns=columns, fmt=fmt)

        answers: list[UsageAnswer] = []
        any_failed = False
        with report_errors(), open_context() as ctx:
            targets = template_targets(spec, identifiers, stdin=stdin, by_id=by_id)
            filters = parse_kv_pairs(filter_, flag="--filter")
            scope = scope_for_command(ctx, organization, spec)
            use = ListTemplateUsage(ctx.workflow_nodes, ctx.repo)
            for target, target_by_id in targets:
                try:
                    answers.append(
                        use(
                            spec,
                            identifier=target,
                            scope=scope,
                            by_id=target_by_id,
                            max_depth=max_depth,
                            filters=filters,
                        )
                    )
                except UntapedError as exc:
                    ctx.diag.failure(exc, item=target)
                    any_failed = True
        trees = [usage_tree(a.target, a.rows, top_level=not filter_) for a in answers]
        diag = Diagnostics()
        for tree in trees:
            for edge in tree.cycle_edges():
                diag.warning(
                    f"workflow {edge.child} runs itself (a cycle)"
                    if edge.child == edge.parent
                    else f"workflow {edge.child} runs workflow {edge.parent}, "
                    "which already runs it (a cycle)"
                )
        rows = [row for answer in answers for row in answer.rows]
        emit(
            rows,
            fmt=fmt,
            columns=columns or (_RAW_COLUMNS if fmt == "raw" else None),
            empty="No containing workflows found.",
            tree=trees,
        )
        if fmt == "tree" and (stopped := sum(row.stopped is not None for row in rows)):
            diag.hint(
                f"pass --recursive to walk up to the top-level workflows ({stopped} stopped)"
                if depth is None
                else f"{plural(stopped, 'workflow')} not read beyond --depth {depth}; "
                "raise --depth to read them"
            )
        finish(any_failed)
