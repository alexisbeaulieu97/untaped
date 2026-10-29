"""One complete preview and confirmation gate for prepared AWX mutations."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from untaped.sdk import Confirm, Failed, Matched, UsageError, plural
from untaped_awx.application.resources.mutation_engine import BatchMutationEngine
from untaped_awx.application.resources.mutation_types import MutationPlan
from untaped_awx.commands._format import format_scope, format_value, outcome_rows
from untaped_awx.commands.context import AwxContext
from untaped_awx.domain import ApplyOutcome
from untaped_awx.domain.outcomes import UNFINISHED_ACTIONS
from untaped_awx.records import ApplyOutcomeRow


@dataclass(frozen=True)
class WriteGate:
    """What a configuration write was asked: its confirmation, batch and verification."""

    confirm: Confirm
    parallel: int = 1
    fail_fast: bool = False
    allow_unverified: bool = False

    def check(self) -> None:
        """Refuse ``--allow-unverified`` without ``--yes`` before reading or writing targets.

        ``--dry-run`` wins over ``--yes``.
        """
        if self.allow_unverified and not self.confirm.yes and not self.confirm.dry_run:
            raise UsageError("--allow-unverified requires --yes")


def preview_lines(previews: Iterable[ApplyOutcome]) -> Iterator[str]:
    """The preview of a prepared batch: each resource's action and changed fields."""
    for outcome in previews:
        yield (
            f"{outcome.kind}/{outcome.name} id={outcome.id} "
            f"scope={format_scope(outcome.scope)}: {outcome.action}"
        )
        for change in outcome.changes:
            yield (
                f"  {change.field}: {format_value(change.before)} → {format_value(change.after)}"
                + (f" ({change.note})" if change.note else "")
            )


def preview_and_execute(
    ctx: AwxContext, engine: BatchMutationEngine, plan: MutationPlan, gate: WriteGate
) -> list[ApplyOutcome]:
    """Show the redacted diffs and ask once, then execute exactly this prepared plan.

    ``--dry-run`` returns the previews; so does a batch with no change.
    """
    previews = [operation.preview for operation in plan.operations]
    if not previews:
        ctx.diag.info("no matching resources; nothing changed")
    changed = sum(outcome.action != "unchanged" for outcome in previews)
    question = f"{plan.mode.capitalize()} {plural(changed, 'resource')}?"
    if not gate.confirm(question, preview=lambda: preview_lines(previews), needed=changed > 0):
        return previews
    if not changed:
        for line in preview_lines(previews):
            ctx.diag.info(line, unmuted=True)
        return previews
    outcomes = engine.execute(plan, fail_fast=gate.fail_fast, parallel=gate.parallel).outcomes
    _report_outcomes(ctx, outcomes)
    return outcomes


def _report_outcomes(ctx: AwxContext, outcomes: list[ApplyOutcome]) -> None:
    """Report each failed write as an attributed error, each other unfinished one as a warning."""
    for outcome in outcomes:
        label = f"{outcome.kind}/{outcome.name}"
        if outcome.error is not None:
            ctx.diag.failure(outcome.error, item=label)
        elif outcome.detail and outcome.action in UNFINISHED_ACTIONS:
            ctx.diag.warning(f"{label}: {outcome.detail}")


def outcome_results(
    outcomes: list[ApplyOutcome],
    *,
    allow_unverified: bool = False,
    predicate_hit: bool = False,
) -> Iterator[ApplyOutcomeRow]:
    """The outcomes as rows, then the run's verdict.

    A write that did not land (or landed unverified, without
    ``--allow-unverified``) fails the run after the rows (:class:`Failed`);
    ``predicate_hit`` (``apply --check`` drift) is :class:`Matched` when
    nothing failed.
    """
    yield from outcome_rows(outcomes)
    if any(
        o.action in UNFINISHED_ACTIONS or (o.unverified and not allow_unverified) for o in outcomes
    ):
        raise Failed
    if predicate_hit:
        raise Matched


def run_mutation_plan(
    ctx: AwxContext, engine: BatchMutationEngine, plan: MutationPlan, gate: WriteGate
) -> Iterator[ApplyOutcomeRow]:
    """Execute the gate and yield its results, then its verdict."""
    outcomes = preview_and_execute(ctx, engine, plan, gate)
    return outcome_results(outcomes, allow_unverified=gate.allow_unverified)
