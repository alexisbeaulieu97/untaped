"""One complete preview and confirmation gate for prepared AWX mutations."""

from __future__ import annotations

from dataclasses import dataclass, replace

from cyclopts import Parameter

from untaped.capabilities.awx.application.mutation_engine import BatchMutationEngine
from untaped.capabilities.awx.application.mutation_types import MutationPlan
from untaped.capabilities.awx.cli.context import AwxContext
from untaped.capabilities.awx.cli.format import format_scope, format_value, outcome_rows
from untaped.capabilities.awx.cli.options import (
    ContinueOption,
    DryRunOption,
    ParallelOption,
    UnverifiedOption,
    YesOption,
)
from untaped.capabilities.awx.domain import ApplyOutcome
from untaped.capability_api import (
    ColumnsOption,
    FormatOption,
    OutputFormat,
    UsageError,
    clamp_parallel,
    echo,
    emit,
    finish,
    plural,
)


def validate_controls(
    *, yes: bool, dry_run: bool, allow_unverified: bool = False, parallel: int = 1
) -> int:
    """Validate write authorization controls before reading or writing targets.

    ``--dry-run`` wins over ``--yes``; ``--parallel`` is already ``>= 1``
    (``ParallelOption``) and is capped here.
    """
    if allow_unverified and not yes and not dry_run:
        raise UsageError("--allow-unverified requires --yes")
    return clamp_parallel(parallel, cap=10, policy="httpx.Limits.max_connections=10")


@Parameter(name="*")
@dataclass(frozen=True, kw_only=True)
class WriteControls:
    """Write authorization and output flags of the configuration commands, in help order."""

    yes: YesOption = False
    dry_run: DryRunOption = False
    continue_on_error: ContinueOption = False
    parallel: ParallelOption = 1
    allow_unverified: UnverifiedOption = False
    fmt: FormatOption = "table"
    columns: ColumnsOption = None

    def validated(self) -> WriteControls:
        """:func:`validate_controls`, with ``parallel`` capped."""
        return replace(
            self,
            parallel=validate_controls(
                yes=self.yes,
                dry_run=self.dry_run,
                allow_unverified=self.allow_unverified,
                parallel=self.parallel,
            ),
        )


CONTROL_DEFAULTS = WriteControls()


def confirm_batch(ctx: AwxContext, *, count: int, verb: str, yes: bool, dry_run: bool) -> bool:
    """Confirm once, with No as the default; the caller has already previewed.

    Returns ``False`` when there is nothing to write (``--dry-run`` or an
    empty batch); a declined prompt raises :class:`OperationCancelledError`.
    """
    if dry_run or count == 0:
        return False
    ctx.progress_ui().confirm_or_cancel(
        f"{verb.capitalize()} {plural(count, 'resource')}?",
        assume_yes=yes,
        refusal=f"{verb} requires --yes or --dry-run when not interactive",
    )
    return True


def emit_outcomes(
    outcomes: list[ApplyOutcome],
    *,
    fmt: OutputFormat,
    columns: list[str] | None,
    allow_unverified: bool = False,
) -> None:
    """Preserve structured identity/status and nonzero failure conventions."""
    emit(outcome_rows(outcomes), fmt=fmt, columns=columns, kind="awx.apply_outcome")
    finish(
        any(
            o.action in {"failed", "partial", "conflict", "skipped"}
            or (o.unverified and not allow_unverified)
            for o in outcomes
        )
    )


def preview_and_execute(
    ctx: AwxContext, engine: BatchMutationEngine, plan: MutationPlan, controls: WriteControls
) -> list[ApplyOutcome]:
    """Display redacted complete diffs, then execute exactly this prepared plan."""
    previews = [operation.preview for operation in plan.operations]
    if not previews:
        echo("No matching resources; no changes.", err=True)
    for outcome in previews:
        echo(
            f"{outcome.kind}/{outcome.name} id={outcome.id} "
            f"scope={format_scope(outcome.scope)}: {outcome.action}",
            err=True,
        )
        for change in outcome.changes:
            echo(
                f"  {change.field}: {format_value(change.before)} → {format_value(change.after)}"
                + (f" ({change.note})" if change.note else ""),
                err=True,
            )
    changed = sum(outcome.action != "unchanged" for outcome in previews)
    if not confirm_batch(
        ctx, count=changed, verb=plan.mode, yes=controls.yes, dry_run=controls.dry_run
    ):
        return previews
    return engine.execute(
        plan, continue_on_error=controls.continue_on_error, parallel=controls.parallel
    ).outcomes


def run_mutation_plan(
    ctx: AwxContext, engine: BatchMutationEngine, plan: MutationPlan, controls: WriteControls
) -> None:
    """Execute the CLI gate and emit its final results and exit status."""
    emit_outcomes(
        preview_and_execute(ctx, engine, plan, controls),
        fmt=controls.fmt,
        columns=controls.columns,
        allow_unverified=controls.allow_unverified,
    )
