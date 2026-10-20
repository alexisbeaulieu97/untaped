"""``recipe apply``: plan a recipe across targets, preview, confirm, and write."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

from cyclopts import Parameter

from untaped.sdk import (
    BatchOutcome,
    ColumnsOption,
    Diagnostics,
    DryRunOption,
    Failure,
    FormatOption,
    ParallelOption,
    StdinOption,
    UntapedError,
    UsageError,
    YesOption,
    batch_apply,
    clamp_parallel,
    emit,
    finish,
    parse_kv_pairs,
    read_stdin,
    ui_context,
    writes,
)
from untaped_recipe.application import RunBulkApply
from untaped_recipe.application.apply_recipe import ApplyRecipe
from untaped_recipe.application.files import read_recipe_file
from untaped_recipe.application.ports import PromptFunc
from untaped_recipe.application.resolution import resolve_apply_recipe
from untaped_recipe.application.run_bulk import resolve_targets
from untaped_recipe.application.targets import Target, resolve_target_lines
from untaped_recipe.cli.common import (
    hook_startup_notice,
    hook_timeout_seconds,
    library_dir,
    merge_vars,
    report_config_errors,
    settings,
)
from untaped_recipe.cli.preview import (
    PlanCounts,
    PreviewMode,
    preview_summary,
    render_preview,
)
from untaped_recipe.domain.plan import TargetPlan
from untaped_recipe.domain.recipe import Recipe
from untaped_recipe.errors import RecipeError
from untaped_recipe.infrastructure import BackupStore, HookExecutor, HookResolver
from untaped_recipe.infrastructure.backup import BackupDraft
from untaped_recipe.infrastructure.file_writer import ApplyWriteError, flush_changes
from untaped_recipe.infrastructure.hook_worker_client import UvHookWorkerPool
from untaped_recipe.infrastructure.pack_store import PackLibrary
from untaped_recipe.records import ApplyOutcome

_diag = Diagnostics()


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
    failed: dict[int, UntapedError]
    backup_id: str | None = None
    cancelled: bool = False


@dataclass(frozen=True)
class TargetInput:
    """Resolved target records plus whether stdin supplied nonblank lines."""

    targets: list[Target]
    stdin_records: bool = False


@writes
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
            help="Input value as KEY=VALUE (repeatable; wins over --vars-file).",
            consume_multiple=False,
        ),
    ] = None,
    vars_file: Annotated[
        list[Path] | None,
        Parameter(
            name="--vars-file",
            negative="",
            help="YAML mapping of input values (repeatable; later files win).",
            consume_multiple=False,
        ),
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
    non_interactive: Annotated[
        bool,
        Parameter(
            name="--non-interactive",
            negative="",
            help="Fail on missing required inputs instead of prompting on a terminal.",
        ),
    ] = False,
    dry_run: Annotated[
        DryRunOption,
        Parameter(
            help=(
                "Preview the changes without writing them. Pack hooks still run "
                "to compute the plan."
            )
        ),
    ] = False,
    check: Annotated[
        bool,
        Parameter(
            name="--check",
            negative="",
            help="Preview and exit 3 when changes would be made; pack hooks still run.",
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
    """Apply a recipe to target directories.

    Every target is planned in memory, previewed on stderr, confirmed, backed
    up, then written. Recipes edit plain directories: they never commit, push
    or run shell steps. Pack hooks run in the pack's own environment through
    `uv run` (exit 4 without `uv`), on every plan, dry runs and checks
    included; built-in hooks such as `yaml_edit` run in-process.

    RECIPE_REF is a path only when it is `.` or `..`, starts with `./`, `../`,
    `/` or `~`, or ends in `.yml`/`.yaml`; anything else is a bare recipe name
    (unique across installed packs) or a `PACK/RECIPE` ref, looked up in the
    library, never on disk. For a local pack directory, pass its path and
    --recipe NAME. How inputs get their values: `untaped agents recipe inputs`.

    A target that fails to plan or write writes nothing and gets a `failed`
    row; the other targets still run (exit 1). Within one target the writes
    are one transaction that rolls back on failure. Declining the
    confirmation exits 1 with `cancelled; no changes made` and `cancelled`
    rows. Each apply writes one backup bundle unless --no-backup
    (`untaped recipe backups restore latest` undoes it).

    --check writes nothing, creates no backups and never prompts. It exits 3
    when any target would change (rows say `planned`) and 1 when any target
    fails; a `skipped` target is neither.

    The preview defaults to a table of changed files (absolute paths, change
    kind, line counts). Past `recipe.preview_max_rows` it collapses to one row
    per target, then truncates with `showing first N of M targets`; --preview
    diff prints unified diffs that `patch` accepts, the only full view then.
    Targets that resolve a sensitive input show no file detail or diff, and
    the value appears as `***` in rows, warnings, errors and backups.

    --parallel (at most 32) plans targets concurrently and sizes the hook
    worker pool. --hook-timeout bounds each hook call; preparing a pack's hook
    environment has its own bound, `recipe.hook_startup_timeout_seconds`, and
    prints `preparing hook environment...`.

    --stdin replaces DIRS; passing both exits 2. Without a terminal it
    refuses before planning (exit 2) unless --yes, --dry-run or --check, and
    input prompts never run. A line is a path unless it is a JSON record: a
    record's target is its absolute `target_path`, else `path`, and records
    whose kind ends in `.summary` are skipped. The same directory given twice,
    in any spelling, is planned once.

    Examples
    --------
    Preview, then write the same plan:

    ```console
    $ untaped recipe apply acme/editorconfig ./api ./web --var python_version=3.14 --dry-run
    $ untaped recipe apply acme/editorconfig ./api ./web --var python_version=3.14 --yes
    ```

    A drift check in CI:

    ```console
    $ untaped recipe apply acme/editorconfig ./api --check
    ```

    Every repo of a workspace:

    ```console
    $ untaped workspace status PROJ-123 -f pipe | untaped recipe apply acme/ci --stdin --dry-run
    ```
    """
    with report_config_errors():
        if stdin and not (yes or dry_run or check):
            # Refuse before any hook runs when the confirmation could never be
            # answered: stdin carries the targets and there is no terminal.
            with ui_context(strict=False).terminal(
                refusal="apply requires --yes when not interactive"
            ):
                pass
        # --check is the CI mode: it asks nothing, like --non-interactive.
        prompt = None if non_interactive or check else _terminal_prompt()
        context = _apply_context(
            recipe_ref,
            dirs=list(dirs or []),
            stdin=stdin,
            raw_vars=var or [],
            vars_files=vars_file or [],
            raw_input_from=input_from or [],
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
            # Human view: key=value pairs and joined warnings instead of reprs,
            # so a view of the records, not the records (it carries no kind).
            emit(
                [_table_row(row.model_dump(mode="json")) for row in rows],
                fmt=fmt,
                columns=columns,
                table_columns=ApplyOutcome.table_columns,
            )
        else:
            emit(rows, fmt=fmt, columns=columns)
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
    vars_files: list[Path],
    raw_input_from: list[str],
    prompt: PromptFunc | None,
    parallel: int,
    hook_timeout_seconds: float,
    recipe_id: str | None = None,
) -> ApplyContext:
    root = library_dir()
    recipe_resolution = resolve_apply_recipe(
        PackLibrary(library_dir=root), recipe, recipe_id=recipe_id
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
    inputs = merge_vars(vars_files, parse_kv_pairs(raw_vars, flag="--var"), file_flag="--vars-file")
    # Prompts run here, serially and before the progress display starts.
    resolved = resolve_targets(
        loaded, targets, inputs=inputs, input_from=_input_sources(raw_input_from), prompt=prompt
    )
    workers = clamp_parallel(parallel, cap=32, policy="recipe planning cap")
    with UvHookWorkerPool(
        max_workers_per_project=workers,
        hook_timeout_seconds=hook_timeout_seconds,
        startup_timeout_seconds=settings().hook_startup_timeout_seconds,
        startup_notice=hook_startup_notice(),
    ) as hook_workers:
        runner = RunBulkApply(
            ApplyRecipe(
                HookExecutor(
                    HookResolver(library_dir=root),
                    workers=hook_workers,
                )
            )
        )
        with _diag.progress("Planning targets") as progress:
            plans = runner.plan_resolved(
                recipe=loaded,
                recipe_dir=recipe_path.parent,
                local_hook_project=recipe_resolution.local_hook_project,
                resolved=resolved,
                parallel=workers,
                on_progress=lambda done, total: progress.update(done=done, total=total),
            )
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
    failed: dict[int, UntapedError] = {}

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
            failed[id(plan)] = exc
            raise

    def _confirm_preview(rows: Sequence[dict[str, object]]) -> None:
        del rows
        _diag.info(preview_summary(context.plans), unmuted=True)

    outcome = batch_apply(
        actionable,
        _apply,
        verb="apply",
        noun="target",
        label=lambda plan: str(plan.target),
        describe=_describe,
        ui=ui_context(strict=False),
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
) -> list[ApplyOutcome]:
    rendered: list[ApplyOutcome] = []
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
        rendered.append(
            _row(plan, action=action, recipe_ref=recipe_ref, failure=execution.failed.get(plan_id))
        )
    return rendered


def _targets(positional: list[Path], *, stdin: bool) -> TargetInput:
    if stdin and positional:
        raise UsageError("provide targets as positional args or via --stdin, not both")
    if not stdin:
        return TargetInput([Target(path=path) for path in positional])
    lines = read_stdin()
    if not lines:
        raise RecipeError("no targets received on stdin")
    return TargetInput(
        resolve_target_lines(list(enumerate(lines, start=1))),
        stdin_records=True,
    )


def _input_sources(raw_sources: list[str]) -> dict[str, str]:
    parsed = parse_kv_pairs(raw_sources, flag="--input-from")
    return {name: str(template) for name, template in parsed.items()}


def _terminal_prompt() -> PromptFunc | None:
    """Prompt for missing required inputs, or ``None`` when stdin is not a terminal.

    Like ``awx test`` variables: never prompt without a TTY (piped ``--stdin``
    targets included); the missing input then fails with a ``--var`` hint.
    """
    ui = ui_context(strict=False)
    if not ui.can_prompt:
        return None

    def ask(message: str, *, sensitive: bool) -> object:
        return ui.secret(message) if sensitive else ui.text(message)

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
    if check:
        (_diag.warning if failed or changed else _diag.info)(
            f"Recipe check: {changed} would change, {unchanged} unchanged"
            f"{skipped_note}, {failed} failed",
        )
        return
    if dry_run:
        (_diag.warning if failed else _diag.info)(
            f"Recipe dry run: {changed} would change, {unchanged} unchanged"
            f"{skipped_note}, {failed} failed",
        )
        return
    if execution.cancelled:
        # finish() prints the standard decline line.
        return
    backup = f", backup {execution.backup_id}" if execution.backup_id else ""
    (_diag.warning if failed else _diag.info)(
        f"Recipe apply: {len(execution.applied)} applied, {unchanged} unchanged"
        f"{skipped_note}, {failed} failed{backup}",
    )


def _table_row(row: dict[str, Any]) -> dict[str, object]:
    """An outcome's table view: warnings joined, inputs as ``key=value`` pairs."""
    inputs = ", ".join(f"{key}={value}" for key, value in row["inputs"].items())
    return row | {"warnings": "; ".join(row["warnings"]), "inputs": inputs}


def _row(
    plan: TargetPlan, *, action: str, recipe_ref: str, failure: Exception | None = None
) -> ApplyOutcome:
    """One outcome row; a planning failure or a write ``failure`` fills ``detail`` and ``error``."""
    detail = plan.error or None
    if failure is not None:
        detail = str(failure)
    elif plan.status == "error":
        failure = plan.failure or RecipeError(plan.error)
    return ApplyOutcome(
        target_path=_absolute(plan.target),
        action=action,
        files_changed=plan.files_changed,
        warnings=list(plan.warnings),
        detail=detail,
        error=None if failure is None else Failure.from_exception(failure, message=detail),
        inputs=dict(plan.display_inputs),
        recipe=recipe_ref,
    )


def _describe(plan: TargetPlan) -> dict[str, object]:
    return {"target_path": str(_absolute(plan.target)), "files_changed": plan.files_changed}


def _absolute(path: Path) -> Path:
    return path if path.is_absolute() else Path.cwd() / path
