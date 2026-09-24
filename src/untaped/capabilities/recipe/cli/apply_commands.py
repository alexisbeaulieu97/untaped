"""``recipe apply``: plan a recipe across targets, preview, confirm, and write."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from cyclopts import Parameter

from untaped.capabilities.recipe.application import RunBulkApply
from untaped.capabilities.recipe.application.apply_recipe import ApplyRecipe
from untaped.capabilities.recipe.application.files import read_recipe_file
from untaped.capabilities.recipe.application.ports import PromptFunc
from untaped.capabilities.recipe.application.resolution import resolve_apply_recipe
from untaped.capabilities.recipe.application.targets import Target, resolve_target_lines
from untaped.capabilities.recipe.cli._context import recipe_ui
from untaped.capabilities.recipe.cli.common import (
    hook_startup_notice,
    hook_timeout_seconds,
    library_root,
    load_yaml_mapping_file,
    report_config_errors,
    settings,
)
from untaped.capabilities.recipe.cli.preview import (
    PlanCounts,
    PreviewMode,
    preview_summary,
    render_preview,
)
from untaped.capabilities.recipe.domain.plan import TargetPlan
from untaped.capabilities.recipe.domain.recipe import Recipe
from untaped.capabilities.recipe.infrastructure import BackupStore, HookExecutor, HookResolver
from untaped.capabilities.recipe.infrastructure.backup import BackupDraft
from untaped.capabilities.recipe.infrastructure.file_writer import ApplyWriteError, flush_changes
from untaped.capabilities.recipe.infrastructure.hook_worker_client import UvHookWorkerPool
from untaped.capabilities.recipe.infrastructure.pack_store import PackLibrary
from untaped.capability_api import (
    AbsolutePath,
    BatchOutcome,
    ColumnsOption,
    ConfigError,
    DryRunOption,
    FormatOption,
    OutcomeRecord,
    ParallelOption,
    StdinOption,
    TargetRecord,
    UntapedError,
    UsageError,
    YesOption,
    batch_apply,
    clamp_parallel,
    echo,
    finish,
    parse_kv_pairs,
    read_stdin,
    render_rows,
    ui_context,
)

MessageKind = Literal["success", "warning", "error", "info"]


class ApplyOutcomeRecord(OutcomeRecord, TargetRecord):
    """One target's ``recipe apply`` result (kind ``recipe.apply_outcome``).

    ``action`` is ``planned`` (preview), ``applied``, ``unchanged``,
    ``skipped`` (a validate hook marked the target not applicable),
    ``cancelled`` (confirmation declined) or ``failed``.
    """

    target_path: AbsolutePath
    files_changed: int
    warnings: list[str]
    error: str | None
    inputs: dict[str, object]
    recipe: str


@dataclass(frozen=True)
class ApplyContext:
    """Prepared apply state."""

    root: Path
    recipe: Recipe
    recipe_ref: str
    plans: list[TargetPlan]


@dataclass(frozen=True)
class ApplyExecution:
    """Executed apply state used for stable row rendering."""

    outcome: BatchOutcome[TargetPlan, TargetPlan]
    applied: frozenset[int]
    failed: dict[int, str]
    backup_id: str | None = None
    cancelled: bool = False


@dataclass(frozen=True)
class TargetInput:
    """Resolved target records plus whether stdin supplied nonblank lines."""

    targets: list[Target]
    stdin_records: bool = False


def apply_command(
    recipe_ref: Annotated[str, Parameter(help="Recipe id, pack/recipe ref, or path.")],
    dirs: Annotated[list[Path] | None, Parameter(help="Target directories.")] = None,
    /,
    *,
    recipe_id: Annotated[
        str | None,
        Parameter(name="--recipe", help="Recipe id when applying a local pack path."),
    ] = None,
    stdin: Annotated[
        StdinOption, Parameter(help="Read target paths, or --format pipe records, from stdin.")
    ] = False,
    var: Annotated[
        list[str] | None,
        Parameter(
            name="--var",
            negative="",
            help="Input override as key=value.",
            consume_multiple=False,
        ),
    ] = None,
    vars_file: Annotated[
        Path | None,
        Parameter(name="--vars-file", help="YAML file containing input overrides."),
    ] = None,
    input_from: Annotated[
        list[str] | None,
        Parameter(
            name="--input-from",
            negative="",
            help="Derive one input from a per-target Jinja expression as key=template.",
            consume_multiple=False,
        ),
    ] = None,
    interactive: Annotated[
        bool,
        Parameter(name="--interactive", negative="", help="Prompt for unresolved inputs."),
    ] = False,
    dry_run: DryRunOption = False,
    check: Annotated[
        bool,
        Parameter(
            name="--check",
            negative="",
            help="Preview and exit 3 when changes would be made.",
        ),
    ] = False,
    yes: YesOption = False,
    backup: Annotated[
        bool,
        Parameter(name="--backup", negative="--no-backup", help="Create backups before writing."),
    ] = True,
    parallel: ParallelOption = 1,
    hook_timeout: Annotated[
        float | None,
        Parameter(name="--hook-timeout", help="Per-hook timeout in seconds; 0 disables."),
    ] = None,
    preview: Annotated[
        PreviewMode | None,
        Parameter(
            name="--preview",
            help=(
                "Preview style: table, diff, or none. "
                "Defaults to none with --check, table otherwise."
            ),
        ),
    ] = None,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Apply a recipe to target directories."""
    with report_config_errors():
        if interactive and check:
            raise UsageError("--interactive cannot be combined with --check")
        if stdin and not (yes or dry_run or check):
            # Refuse before any hook runs when the confirmation could never be
            # answered: stdin carries the targets and there is no terminal.
            with recipe_ui().terminal(refusal="apply requires --yes when not interactive"):
                pass
        with ExitStack() as stack:
            prompt = _interactive_prompt(interactive=interactive, stack=stack)
            context = _apply_context(
                recipe_ref,
                dirs=list(dirs or []),
                stdin=stdin,
                raw_vars=var or [],
                vars_file=vars_file,
                raw_input_from=input_from or [],
                interactive=interactive,
                prompt=prompt,
                parallel=parallel,
                hook_timeout_seconds=hook_timeout_seconds(hook_timeout),
                recipe_id=recipe_id,
            )
            render_preview(
                context.recipe,
                context.plans,
                # --check defaults to no preview; everything else to a table.
                preview=preview or ("none" if check else "table"),
                preview_max_rows=settings().preview_max_rows,
            )
            outcome = _execute_plans(
                context,
                backup=backup and not check,
                yes=yes or check,
                dry_run=dry_run or check,
            )
        rows = _outcome_rows(
            context.plans,
            outcome,
            recipe_ref=context.recipe_ref,
            preview=dry_run or check,
        )
        if fmt == "table":
            # Human view: key=value pairs and joined warnings instead of reprs.
            # Structured formats keep the real mapping and list.
            rows = [
                {
                    **row,
                    "warnings": "; ".join(_strings(row["warnings"])),
                    "inputs": _inputs_cell(row["inputs"]),
                }
                for row in rows
            ]
        rendered = render_rows(rows, fmt=fmt, columns=columns, kind="recipe.apply_outcome")
        if rendered:
            echo(rendered)
        _render_result_summary(context.plans, outcome, check=check, dry_run=dry_run)
        if outcome.cancelled:
            finish(outcome.outcome)
        has_errors = any(plan.status == "error" for plan in context.plans)
        has_drift = check and any(plan.status != "error" and plan.changes for plan in context.plans)
        finish(has_errors or outcome.outcome.any_failed, predicate_hit=has_drift)


def _apply_context(
    recipe: str,
    *,
    dirs: list[Path],
    stdin: bool,
    raw_vars: list[str],
    vars_file: Path | None,
    raw_input_from: list[str],
    interactive: bool,
    prompt: PromptFunc | None,
    parallel: int,
    hook_timeout_seconds: float,
    recipe_id: str | None = None,
) -> ApplyContext:
    root = library_root()
    recipe_resolution = resolve_apply_recipe(
        PackLibrary(library_root=root), recipe, recipe_id=recipe_id
    )
    recipe_path = recipe_resolution.path
    loaded = read_recipe_file(recipe_path)
    target_input = _targets(dirs, stdin=stdin)
    targets = target_input.targets
    if not targets:
        if target_input.stdin_records:
            return ApplyContext(
                root=root,
                recipe=loaded,
                recipe_ref=recipe_resolution.ref,
                plans=[],
            )
        raise UsageError("at least one target directory is required (or use --stdin)")
    inputs = _input_values(raw_vars, vars_file)
    input_from = _input_sources(raw_input_from)
    workers = clamp_parallel(parallel, cap=32, policy="recipe planning cap")
    ui = recipe_ui()
    with UvHookWorkerPool(
        max_workers_per_project=workers,
        hook_timeout_seconds=hook_timeout_seconds,
        startup_timeout_seconds=settings().hook_startup_timeout_seconds,
        startup_notice=hook_startup_notice(ui),
    ) as hook_workers:
        runner = RunBulkApply(
            ApplyRecipe(
                HookExecutor(
                    HookResolver(library_root=root),
                    workers=hook_workers,
                )
            )
        )
        with ui.progress("Planning targets") as progress:
            try:
                plans = runner.plan(
                    recipe=loaded,
                    recipe_dir=recipe_path.parent,
                    local_hook_project=recipe_resolution.local_hook_project,
                    targets=targets,
                    inputs=inputs,
                    input_from=input_from,
                    interactive=interactive,
                    prompt=prompt,
                    parallel=workers,
                    on_progress=lambda done, total: progress.update(
                        f"{done}/{total}",
                        fraction=done / total if total else None,
                    ),
                )
            except ValueError as exc:
                raise ConfigError(str(exc)) from exc
    return ApplyContext(
        root=root,
        recipe=loaded,
        recipe_ref=recipe_resolution.ref,
        plans=plans,
    )


def _execute_plans(
    context: ApplyContext,
    *,
    backup: bool,
    yes: bool,
    dry_run: bool,
) -> ApplyExecution:
    actionable = [plan for plan in context.plans if plan.status != "error" and plan.changes]
    store = BackupStore(context.root / "backups")
    draft: BackupDraft | None = None
    applied: set[int] = set()
    failed: dict[int, str] = {}

    def _apply(plan: TargetPlan) -> TargetPlan:
        nonlocal draft
        reservation = None
        try:
            if backup:
                if draft is None:
                    draft = store.start(
                        recipe_name=context.recipe_ref,
                        inputs={},
                    )
                reservation = draft.stage(plan.changes, inputs=plan.display_inputs)
            flush_changes(plan.changes)
            if reservation is not None and draft is not None:
                draft.commit(reservation)
            applied.add(id(plan))
            return plan
        except UntapedError as exc:
            if (
                reservation is not None
                and draft is not None
                and isinstance(exc, ApplyWriteError)
                and exc.rollback_incomplete
            ):
                draft.commit(reservation)
            failed[id(plan)] = str(exc)
            raise

    def _confirm_preview(rows: Sequence[dict[str, object]]) -> None:
        del rows
        recipe_ui().message("info", preview_summary(context.plans))

    outcome = batch_apply(
        actionable,
        _apply,
        verb="apply",
        noun="target",
        label=lambda plan: str(plan.target),
        describe=_describe,
        ui=recipe_ui(),
        destructive=True,
        assume_yes=yes,
        preview_only=dry_run,
        render_generic_preview=False,
        preview=_confirm_preview,
    )
    backup_id = draft.id if draft is not None and draft.entries else None
    if draft is not None:
        draft.discard_if_empty()
    return ApplyExecution(
        outcome=outcome,
        applied=frozenset(applied),
        failed=failed,
        backup_id=backup_id,
        cancelled=outcome.cancelled,
    )


def _outcome_rows(
    plans: list[TargetPlan],
    execution: ApplyExecution,
    *,
    recipe_ref: str,
    preview: bool,
) -> list[dict[str, object]]:
    rendered: list[dict[str, object]] = []
    for plan in plans:
        plan_id = id(plan)
        if plan.status == "skipped":
            # Not applicable: never a failure, and never touched.
            action = "skipped"
        elif plan.status == "error":
            # A planning error is never "unchanged".
            action = "failed"
        elif not plan.changes:
            action = "unchanged"
        elif preview:
            action = "planned"
        elif execution.cancelled:
            # Declined confirmation: nothing ran.
            action = "cancelled"
        elif plan_id in execution.failed:
            action = "failed"
        elif plan_id in execution.applied:
            action = "applied"
        else:
            action = "unchanged"
        row = _row(plan, action=action, recipe_ref=recipe_ref)
        if plan_id in execution.failed:
            row["error"] = execution.failed[plan_id]
        rendered.append(row)
    return rendered


def _targets(positional: list[Path], *, stdin: bool) -> TargetInput:
    if stdin and positional:
        raise UsageError("provide targets as positional args or via --stdin, not both")
    if not stdin:
        return TargetInput([Target(path=path) for path in positional])
    lines = read_stdin()
    if not lines:
        raise ConfigError("no targets received on stdin")
    try:
        return TargetInput(
            resolve_target_lines(list(enumerate(lines, start=1))),
            stdin_records=True,
        )
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc


def _input_values(raw_vars: list[str], vars_file: Path | None) -> dict[str, object]:
    values: dict[str, object] = {}
    if vars_file is not None:
        values.update(load_yaml_mapping_file(vars_file, flag="--vars-file"))
    values.update(parse_kv_pairs(raw_vars, flag="--var"))
    return values


def _input_sources(raw_sources: list[str]) -> dict[str, str]:
    parsed = parse_kv_pairs(raw_sources, flag="--input-from")
    return {name: str(template) for name, template in parsed.items()}


def _interactive_prompt(
    *,
    interactive: bool,
    stack: ExitStack,
) -> PromptFunc | None:
    if not interactive:
        return None
    ui = stack.enter_context(
        ui_context(strict=True).terminal(refusal="interactive input requires a terminal")
    )

    def ask(
        message: str,
        *,
        sensitive: bool,
        default: object | None = None,
        required: bool = True,
    ) -> object:
        if sensitive:
            return ui.secret(message, required=required)
        text_default = None if default is None else str(default)
        return ui.text(message, default=text_default, required=required)

    return ask


def _render_result_summary(
    plans: list[TargetPlan],
    execution: ApplyExecution,
    *,
    check: bool,
    dry_run: bool,
) -> None:
    counts = PlanCounts.of(plans)
    failed = counts.failed + len(execution.failed)
    changed, unchanged = counts.changing, counts.unchanged
    skipped_note = f", {counts.skipped} skipped" if counts.skipped else ""
    ui = recipe_ui()
    if check:
        kind: MessageKind = "warning" if failed or changed else "info"
        ui.message(
            kind,
            f"Recipe check: {changed} would change, {unchanged} unchanged"
            f"{skipped_note}, {failed} failed",
        )
        return
    if dry_run:
        kind = "warning" if failed else "info"
        ui.message(
            kind,
            f"Recipe dry run: {changed} would change, {unchanged} unchanged"
            f"{skipped_note}, {failed} failed",
        )
        return
    if execution.cancelled:
        # finish() prints the standard decline line.
        return
    kind = "warning" if failed else "info"
    backup = f", backup {execution.backup_id}" if execution.backup_id else ""
    ui.message(
        kind,
        f"Recipe apply: {len(execution.applied)} applied, {unchanged} unchanged"
        f"{skipped_note}, {failed} failed{backup}",
    )


def _inputs_cell(inputs: object) -> str:
    if not isinstance(inputs, dict) or not inputs:
        return ""
    return ", ".join(f"{key}={value}" for key, value in inputs.items())


def _strings(value: object) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _row(plan: TargetPlan, *, action: str, recipe_ref: str) -> dict[str, object]:
    return ApplyOutcomeRecord(
        target_path=_absolute(plan.target),
        action=action,
        files_changed=plan.files_changed,
        warnings=list(plan.warnings),
        error=plan.error or None,
        inputs=dict(plan.display_inputs),
        recipe=recipe_ref,
    ).model_dump(mode="json")


def _describe(plan: TargetPlan) -> dict[str, object]:
    return {"target_path": str(_absolute(plan.target)), "files_changed": plan.files_changed}


def _absolute(path: Path) -> Path:
    return path if path.is_absolute() else Path.cwd() / path
