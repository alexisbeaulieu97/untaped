"""One complete preview and confirmation gate for prepared AWX mutations."""

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager

from untaped.api import ConfigError, OutputFormat, UiContext, clamp_parallel, echo, emit, finish
from untaped.capabilities.awx.application.mutation_engine import BatchMutationEngine
from untaped.capabilities.awx.application.mutation_types import MutationPlan
from untaped.capabilities.awx.cli._context import AwxContext
from untaped.capabilities.awx.cli.format import outcome_rows
from untaped.capabilities.awx.domain import ApplyOutcome


def validate_controls(
    *, yes: bool, dry_run: bool, allow_unverified: bool = False, parallel: int = 1
) -> int:
    """Validate write authorization controls before reading or writing targets."""
    if yes and dry_run:
        raise ConfigError("--yes and --dry-run are mutually exclusive")
    if allow_unverified and not yes:
        raise ConfigError("--allow-unverified requires --yes")
    if parallel < 1:
        raise ConfigError("--parallel must be >= 1")
    return clamp_parallel(parallel, cap=10, policy="httpx.Limits.max_connections=10")


@contextmanager
def prompt_ui(ctx: AwxContext) -> Iterator[UiContext]:
    """Use the controlling terminal independently of consumed pipeline input."""
    ui = ctx.progress_ui()
    if ui.stdin.isatty():
        yield ui
        return
    with ExitStack() as stack:
        try:
            terminal = stack.enter_context(open("/dev/tty", encoding="utf-8"))
        except OSError as exc:
            raise ConfigError("confirmation requires a terminal; use --yes or --dry-run") from exc
        original = ui.stdin
        try:
            ui.stdin = terminal
            yield ui
        finally:
            ui.stdin = original


def confirm_batch(ctx: AwxContext, *, count: int, verb: str, yes: bool, dry_run: bool) -> bool:
    """Confirm once, with No as the default; the caller has already previewed."""
    if dry_run or count == 0:
        return False
    if yes:
        return True
    with prompt_ui(ctx) as ui:
        return ui.confirm(f"{verb.capitalize()} {count} resource(s)?", default=False)


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
    ctx: AwxContext,
    engine: BatchMutationEngine,
    plan: MutationPlan,
    *,
    yes: bool = False,
    dry_run: bool = False,
    continue_on_error: bool = False,
    parallel: int = 1,
) -> list[ApplyOutcome]:
    """Display redacted complete diffs, then execute exactly this prepared plan."""
    previews = [operation.preview for operation in plan.operations]
    if not previews:
        echo("No matching resources; no changes.", err=True)
    for outcome in previews:
        echo(
            f"{outcome.kind}/{outcome.name} id={outcome.id} "
            f"scope={outcome.scope}: {outcome.action}",
            err=True,
        )
        for change in outcome.changes:
            echo(
                f"  {change.field}: {change.before!r} → {change.after!r}"
                + (f" ({change.note})" if change.note else ""),
                err=True,
            )
    changed = sum(outcome.action != "unchanged" for outcome in previews)
    outcomes = previews
    if confirm_batch(ctx, count=changed, verb=plan.mode, yes=yes, dry_run=dry_run):
        outcomes = engine.execute(
            plan, continue_on_error=continue_on_error, parallel=parallel
        ).outcomes
    elif changed and not dry_run:
        echo("Cancelled; no changes written.", err=True)
    return outcomes


def run_mutation_plan(
    ctx: AwxContext,
    engine: BatchMutationEngine,
    plan: MutationPlan,
    *,
    yes: bool = False,
    dry_run: bool = False,
    continue_on_error: bool = False,
    parallel: int = 1,
    allow_unverified: bool = False,
    fmt: OutputFormat = "table",
    columns: list[str] | None = None,
) -> None:
    """Execute the CLI gate and emit its final results and exit status."""
    outcomes = preview_and_execute(
        ctx,
        engine,
        plan,
        yes=yes,
        dry_run=dry_run,
        continue_on_error=continue_on_error,
        parallel=parallel,
    )
    emit_outcomes(outcomes, fmt=fmt, columns=columns, allow_unverified=allow_unverified)
