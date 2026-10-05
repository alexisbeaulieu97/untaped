"""One complete preview and confirmation gate for prepared AWX mutations."""

from __future__ import annotations

from dataclasses import dataclass, replace

from cyclopts import Parameter

from untaped.sdk import (
    ColumnsOption,
    FormatOption,
    OutputFormat,
    UsageError,
    clamp_parallel,
    echo,
    emit,
    finish,
    plural,
    report_error,
)
from untaped_awx.application.mutation_engine import BatchMutationEngine
from untaped_awx.application.mutation_types import MutationPlan
from untaped_awx.cli.context import AwxContext
from untaped_awx.cli.format import (
    OUTCOME_TABLE_COLUMNS,
    format_scope,
    format_value,
    outcome_rows,
)
from untaped_awx.cli.options import (
    ContinueOption,
    DryRunOption,
    ParallelOption,
    UnverifiedOption,
    YesOption,
)
from untaped_awx.domain import ApplyOutcome


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


#: Outcome actions that fail the run: the write did not fully land.
_UNFINISHED = frozenset({"failed", "partial", "conflict", "skipped"})


def emit_outcomes(
    outcomes: list[ApplyOutcome],
    *,
    fmt: OutputFormat,
    columns: list[str] | None,
    allow_unverified: bool = False,
    predicate_hit: bool = False,
    per_kind: bool = False,
) -> None:
    """Preserve structured identity/status and nonzero failure conventions.

    ``predicate_hit`` (``apply --check`` drift) exits 3 when nothing failed.
    ``per_kind`` (a ``<kind>`` command) leaves the constant ``kind`` out of the table.
    """
    emit(
        outcome_rows(outcomes),
        fmt=fmt,
        columns=columns,
        table_columns=[c for c in OUTCOME_TABLE_COLUMNS if not (per_kind and c == "kind")],
        kind="awx.apply_outcome",
    )
    finish(
        any(o.action in _UNFINISHED or (o.unverified and not allow_unverified) for o in outcomes),
        predicate_hit=predicate_hit,
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
    outcomes = engine.execute(
        plan, continue_on_error=controls.continue_on_error, parallel=controls.parallel
    ).outcomes
    _report_outcomes(ctx, outcomes)
    return outcomes


def _report_outcomes(ctx: AwxContext, outcomes: list[ApplyOutcome]) -> None:
    """Report each failed write as an attributed error, each other unfinished one as a warning."""
    for outcome in outcomes:
        label = f"{outcome.kind}/{outcome.name}"
        if outcome.error is not None:
            report_error(outcome.error, item=label)
        elif outcome.detail and outcome.action in _UNFINISHED:
            ctx.progress_ui().message("warning", f"{label}: {outcome.detail}")


def run_mutation_plan(
    ctx: AwxContext,
    engine: BatchMutationEngine,
    plan: MutationPlan,
    controls: WriteControls,
    *,
    per_kind: bool = False,
) -> None:
    """Execute the CLI gate and emit its final results and exit status."""
    emit_outcomes(
        preview_and_execute(ctx, engine, plan, controls),
        fmt=controls.fmt,
        columns=controls.columns,
        allow_unverified=controls.allow_unverified,
        per_kind=per_kind,
    )
