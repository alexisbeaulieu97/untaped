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
from untaped.capabilities.awx.application import ListTemplateUsage
from untaped.capabilities.awx.cli._context import open_context, scope_for_command
from untaped.capabilities.awx.cli._pipe import id_field_for
from untaped.capabilities.awx.cli.options import ByIdOption, OrganizationOption, resolve_max_depth
from untaped.capabilities.awx.domain import WorkflowUsage
from untaped.capabilities.awx.infrastructure.spec import AwxResourceSpec

_DEFAULT_COLUMNS = ["id", "name", "depth", "node_count"]


def register_usage_command(parent: App, spec: AwxResourceSpec) -> None:
    """Register the ``usage`` command on a template sub-app."""

    @parent.command(name="usage")
    def usage_command(
        identifiers: Annotated[
            list[str] | None,
            Parameter(
                help=(
                    "Template name(s) — one or more, or omit and pass "
                    "``--stdin``. Pass ``--by-id`` to resolve AWX ids "
                    "instead. Multiple targets concatenate their usage "
                    "rows in the order given (dedup is per target)."
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
                    "Read template names from stdin (one per line); "
                    "equivalent to passing them positionally. Per-target "
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
                name=["--recursive", "-r"],
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
            ),
        ] = None,
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """List the workflow job templates that contain one or more templates."""
        max_depth = resolve_max_depth(depth, recursive)

        usages: list[WorkflowUsage] = []
        any_failed = False
        with report_errors(), open_context() as ctx:
            targets = read_identifiers(
                list(identifiers or []),
                stdin=stdin,
                id_field=id_field_for(spec, by_id=by_id),
            )
            filters = parse_kv_pairs(filter_, flag="--filter")
            scope = scope_for_command(ctx, organization, spec)
            use = ListTemplateUsage(
                ctx.workflow_nodes,
                ctx.repo,
                warn=lambda msg: echo(f"warning: {msg}", err=True),
            )
            for target in targets:
                try:
                    usages.extend(
                        use(
                            spec,
                            identifier=target,
                            scope=scope,
                            by_id=by_id,
                            max_depth=max_depth,
                            filters=filters,
                        )
                    )
                except UntapedError as exc:
                    echo(f"warning: {target}: {exc}", err=True)
                    any_failed = True
        rows = [u.model_dump() for u in usages]
        cols = list(columns) if columns else list(_DEFAULT_COLUMNS)
        emit(rows, fmt=fmt, columns=cols, kind="awx.template_usage")
        finish(any_failed)
