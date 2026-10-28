"""``untaped recipe apply``: plan a recipe across targets, preview, confirm, and write."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, Field

from untaped.sdk import (
    Confirm,
    Document,
    ExistingFile,
    Failure,
    KeyValue,
    Matched,
    Output,
    Parallel,
    Positional,
    Record,
    Stdin,
    UntapedError,
    UsageError,
    plural,
)
from untaped_recipe.application import RunBulkApply
from untaped_recipe.application.apply_recipe import ApplyRecipe
from untaped_recipe.application.files import read_recipe_file
from untaped_recipe.application.ports import PromptFunc
from untaped_recipe.application.resolution import resolve_apply_recipe
from untaped_recipe.application.run_bulk import resolve_targets
from untaped_recipe.application.targets import Target, target_of
from untaped_recipe.commands._recipe import (
    RECIPE_REF,
    Hooks,
    RecipeCommand,
    merge_vars,
    recipe_errors,
)
from untaped_recipe.commands.preview import PlanCounts, PreviewMode, preview_lines
from untaped_recipe.domain.plan import TargetPlan
from untaped_recipe.domain.recipe import RecipeFile
from untaped_recipe.errors import RecipeError
from untaped_recipe.infrastructure.backup import BackupDraft
from untaped_recipe.infrastructure.file_writer import ApplyWriteError, flush_changes
from untaped_recipe.records import ApplyOutcome


class Inputs(BaseModel):
    """Where each recipe input's value comes from, how values are parsed, and when it prompts.

    Read before passing inputs to `recipe apply`.

    ## Where a value comes from

    For each input, the first of these that gives a value wins:

    1. `--var`, `--vars-file` or `--input-from`;
    2. the recipe's `from` candidates;
    3. the recipe's `default`;
    4. a prompt, for a required input only;
    5. otherwise a `missing required input` error.

    An input name the recipe doesn't declare is rejected. `untaped recipe get
    REF` lists a recipe's inputs.

    ## How values are parsed

    - `--var` parses its value as YAML only for inputs declared `list` or
      `dict` (`--var 'cols=[name, owner]'`); a scalar input takes the literal
      string.
    - `--vars-file` values are YAML, so `3.10` becomes `3.1`: quote
      version-like strings.

    ## Values per target

    `--input-from NAME='<expression>'` derives one input per target. It must
    resolve for every target, where a recipe's `from` candidates fall through
    silently to the next. It can't be combined with `--var` for the same input,
    and an input with `scope: global` rejects it.

    Expressions (a recipe's `from` and `--input-from`) allow literal text,
    constants and field access on `target.path`, `target.name`,
    `target.parent_path`, `target.parent_name` and `record` (the piped row
    that named the target). Filters, operators, calls and control blocks are
    rejected. With `--stdin`, upstream output can choose both the targets and
    their values.

    ```console
    $ untaped recipe apply acme/readme ./api ./web --input-from 'title={{ target.name }}' --dry-run
    ```

    ## Prompts

    Only a required input with no value prompts, and only when stdin is a
    terminal; a sensitive input prompts hidden, and `list`/`dict` inputs never
    prompt. Prompts run in target order before planning starts.

    With no terminal (piped `--stdin` included), `--non-interactive` or
    `--check`, a missing input fails with `missing required input: NAME`: a
    global input fails the run, a target input fails that target's row.
    """

    var: list[KeyValue] = Field(
        default_factory=list,
        description="Input value as KEY=VALUE (repeatable; wins over --vars-file).",
    )
    vars_file: list[ExistingFile] = Field(
        default_factory=list,
        description="YAML mapping of input values (repeatable; later files win).",
    )
    input_from: list[KeyValue] = Field(
        default_factory=list,
        description="Derive one input from a per-target Jinja expression as KEY=TEMPLATE.",
    )
    non_interactive: bool = Field(
        False, description="Fail on missing required inputs instead of prompting on a terminal."
    )

    def values(self) -> dict[str, object]:
        """The ``--vars-file`` mappings in order, then ``--var`` pairs over them."""
        return merge_vars(self.vars_file, dict(self.var), flag="--vars-file")


@dataclass
class _Execution:
    """What writing the plans did: which applied, which failed, and the backup taken."""

    applied: set[int] = field(default_factory=set)
    failed: dict[int, UntapedError] = field(default_factory=dict)
    backup_id: str | None = None


class Apply(RecipeCommand):
    """Apply a recipe to target directories.

    Every target is planned in memory, previewed on stderr, confirmed, backed
    up, then written. Recipes edit plain directories: they never commit, push
    or run shell steps.

    RECIPE_REF is a path only when it is `.` or `..`, starts with `./`, `../`, `/`
    or `~`, or ends in `.yml`/`.yaml`; anything else is a bare recipe name
    (unique across installed packs) or a `PACK/RECIPE` ref, looked up in the
    library, never on disk. For a local pack directory, pass its path and
    --recipe NAME.

    A target that fails to plan or write writes nothing and gets a `failed`
    row; the other targets still run (exit 1). Within one target the writes
    are one transaction that rolls back on failure. Each apply writes one
    backup bundle unless --no-backup (`untaped recipe backups restore latest`
    undoes it).

    --dry-run and --check write nothing. Pack hooks still run to compute the
    plan. --check also creates no backups and never prompts. It exits 3
    when any target would change (rows say `planned`) and 1 when any target
    fails; a `skipped` target is neither.

    The preview defaults to a table of changed files (absolute paths, change
    kind, line counts). Past `recipe.preview_max_rows` it collapses to one row
    per target, then truncates with `showing first N of M targets`; --preview
    diff prints unified diffs that `patch` accepts, the only full view then.
    Targets that resolve a sensitive input show no file detail or diff, and
    the value appears as `***` in rows, warnings, errors and backup
    metadata; templates and hooks still get the real value, so the written
    files can hold it.

    --parallel (at most 32) plans targets concurrently and sizes the hook
    worker pool.

    --stdin replaces DIRS; passing both exits 2. With --stdin the
    confirmation reads the controlling terminal and input prompts never run;
    without a terminal it refuses before planning (exit 2) unless --yes,
    --dry-run or --check. A line is a path, or a row of any kind: its target
    is its absolute `target_path`, else its `path`. The same directory given
    twice, in any spelling, is planned once.

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

    recipe_ref: Annotated[Positional[str], Document(RecipeFile)] = Field(description=RECIPE_REF)
    dirs: Positional[list[Path]] = Field(default_factory=list, description="Target directories.")
    recipe: str | None = Field(None, description="Recipe name when applying a local pack path.")
    stdin: Stdin[Record]
    inputs: Inputs = Field(default_factory=Inputs)
    hooks: Hooks = Field(default_factory=Hooks)
    check: bool = Field(
        False, description="Preview and exit 3 when changes would be made; pack hooks still run."
    )
    backup: bool = Field(True, description="Back up each file before writing it.")
    preview: PreviewMode | None = Field(
        None,
        description="Preview style: table, diff, or none (default: none with --check, else table).",
    )
    confirm: Confirm
    parallel: Parallel = Parallel(default=1, cap=32)
    output: Output[ApplyOutcome]

    def run(self) -> Iterator[ApplyOutcome]:
        confirm = self.confirm.as_dry_run() if self.check else self.confirm
        with recipe_errors():
            if self.stdin.requested:  # before any hook runs, when nobody could answer
                confirm.ensure_answerable("Apply to the targets?")
            resolved = resolve_apply_recipe(self.library(), self.recipe_ref, recipe_id=self.recipe)
            loaded = read_recipe_file(resolved.path)
            targets = self._targets()
            plans = self._plan(loaded, resolved.path, resolved.local_hook_project, targets)
            preview = self.preview or ("none" if self.check else "table")
            lines = self._preview(loaded, plans, preview)
            for line in lines:
                self.ctx.diag.info(line, unmuted=True)
            todo = [plan for plan in plans if plan.status != "error" and plan.changes]
            if todo and len(lines) > 1 and not (confirm.yes or confirm.dry_run):
                # The summary again, beside the question a long preview pushed away.
                self.ctx.diag.info(lines[0], unmuted=True)
            go = confirm(f"Apply to {plural(len(todo), 'target')}?", needed=bool(todo))
            execution = self._write(todo, ref=resolved.ref) if go else _Execution()
        rows = [_row(plan, execution, ref=resolved.ref, planned=not go) for plan in plans]
        self._summary(plans, execution, planned=not go)
        yield from rows
        failed = any(row.failed for row in rows)
        if self.check and not failed and any(row.action == "planned" for row in rows):
            raise Matched

    def _targets(self) -> list[Target]:
        if not self.stdin.requested:
            if not self.dirs:
                raise UsageError("at least one target directory is required (or use --stdin)")
            return [Target(path=path) for path in self.dirs]
        if self.dirs:
            raise UsageError("provide targets as positional args or via --stdin, not both")
        targets: list[Target] = []
        for number, value in enumerate(self.stdin.values(), 1):
            if isinstance(value, str):
                targets.append(Target(Path(value)))
                continue
            try:
                targets.append(target_of(value))
            except ValueError as exc:
                raise ValueError(f"line {number}: {exc}") from exc
        return targets

    def _plan(
        self,
        loaded: RecipeFile,
        recipe_path: Path,
        local_hook_project: Path | None,
        targets: list[Target],
    ) -> list[TargetPlan]:
        # Prompts run here, serially and before the progress display starts.
        resolved = resolve_targets(
            loaded,
            targets,
            inputs=self.inputs.values(),
            input_from={name: template for name, template in self.inputs.input_from},
            prompt=self._prompt(),
        )
        workers = self.parallel.value
        with self.executor(self.hooks, workers=workers) as executor:
            runner = RunBulkApply(ApplyRecipe(executor))
            with self.ctx.diag.progress("Planning targets") as progress:
                return runner.plan_resolved(
                    recipe=loaded,
                    recipe_dir=recipe_path.parent,
                    local_hook_project=local_hook_project,
                    resolved=resolved,
                    parallel=workers,
                    on_progress=lambda done, total: progress.update(done=done, total=total),
                )

    def _prompt(self) -> PromptFunc | None:
        """Ask for a missing required input on a terminal; ``None`` when nothing may ask.

        Never without a TTY stdin (piped ``--stdin`` included), under
        ``--non-interactive``, or under ``--check``, the mode for CI: the
        missing input then fails with a ``--var`` hint.
        """
        terminal = self.ctx.terminal
        if self.inputs.non_interactive or self.check or not terminal.can_prompt:
            return None

        def ask(message: str, *, sensitive: bool) -> object:
            return terminal.secret(message) if sensitive else terminal.text(message)

        return ask

    def _preview(
        self, loaded: RecipeFile, plans: list[TargetPlan], preview: PreviewMode
    ) -> list[str]:
        return preview_lines(
            loaded,
            plans,
            preview=preview,
            preview_max_rows=self.settings.preview_max_rows,
        )

    def _write(self, todo: list[TargetPlan], *, ref: str) -> _Execution:
        """Back up and write each target's changes; one target's failure leaves the others."""
        execution = _Execution()
        store = self.backups()
        draft: BackupDraft | None = None
        for plan in todo:
            reservation = None
            try:
                if self.backup:
                    if draft is None:
                        draft = store.start(recipe_name=ref, inputs={})
                    reservation = draft.stage(plan.changes, inputs=plan.display_inputs)
                flush_changes(plan.changes)
                if reservation is not None and draft is not None:
                    draft.commit(reservation)
                execution.applied.add(id(plan))
            except UntapedError as exc:
                if (
                    reservation is not None
                    and draft is not None
                    and isinstance(exc, ApplyWriteError)
                    and exc.rollback_incomplete
                ):
                    draft.commit(reservation)
                self.ctx.diag.failure(exc, item=str(plan.target))
                execution.failed[id(plan)] = exc
        if draft is not None:
            execution.backup_id = draft.id if draft.entries else None
            draft.discard_if_empty()
        return execution

    def _summary(self, plans: list[TargetPlan], execution: _Execution, *, planned: bool) -> None:
        counts = PlanCounts.of(plans)
        failed = counts.failed + len(execution.failed)
        skipped = f", {counts.skipped} skipped" if counts.skipped else ""
        if planned:
            what = "check" if self.check else "dry run"
            text = (
                f"Recipe {what}: {counts.changing} would change, {counts.unchanged} unchanged"
                f"{skipped}, {failed} failed"
            )
            warn = failed or (self.check and counts.changing)
        else:
            backup = f", backup {execution.backup_id}" if execution.backup_id else ""
            text = (
                f"Recipe apply: {len(execution.applied)} applied, {counts.unchanged} unchanged"
                f"{skipped}, {failed} failed{backup}"
            )
            warn = failed
        (self.ctx.diag.warning if warn else self.ctx.diag.info)(text)


def _row(plan: TargetPlan, execution: _Execution, *, ref: str, planned: bool) -> ApplyOutcome:
    """One target's row; a planning failure or a write failure fills ``detail`` and ``error``."""
    failure: Exception | None = execution.failed.get(id(plan))
    if plan.status == "skipped":
        action = "skipped"  # not applicable: never a failure, and never touched
    elif plan.status == "error":
        action = "failed"  # a planning error is never "unchanged"
    elif not plan.changes:
        action = "unchanged"
    elif planned:
        action = "planned"
    elif failure is not None:
        action = "failed"
    elif id(plan) in execution.applied:
        action = "applied"
    else:
        action = "unchanged"
    detail = plan.error or None
    if failure is not None:
        detail = str(failure)
    elif plan.status == "error":
        failure = plan.failure or RecipeError(plan.error)
    target = plan.target if plan.target.is_absolute() else Path.cwd() / plan.target
    return ApplyOutcome(
        target_path=target,
        action=action,
        files_changed=plan.files_changed,
        warnings=list(plan.warnings),
        detail=detail,
        error=None if failure is None else Failure.from_exception(failure, message=detail),
        inputs=dict(plan.display_inputs),
        recipe=ref,
    )
