"""Bulk apply orchestration for planned target changes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from untaped.capabilities.recipe.application.apply_recipe import ApplyRecipe
from untaped.capabilities.recipe.application.inputs import (
    InputResolutionResult,
    has_sensitive_inputs,
    prepare_input_resolution,
    resolve_global_values,
    resolve_target_inputs,
)
from untaped.capabilities.recipe.application.ports import PromptFunc
from untaped.capabilities.recipe.application.targets import Target, dedupe_targets
from untaped.capabilities.recipe.domain.plan import TargetPlan
from untaped.capabilities.recipe.domain.recipe import Recipe
from untaped.capabilities.recipe.errors import RecipeError
from untaped.sdk import attribution, bounded_map

SENSITIVE_DIAGNOSTIC_SUPPRESSED = "diagnostic suppressed for target with sensitive inputs"
SENSITIVE_ERROR_SUPPRESSED = (
    "target planning failed; diagnostic suppressed for target with sensitive inputs"
)


@dataclass(frozen=True)
class ResolvedTarget:
    """One deduplicated target with its resolved inputs, or the error resolving them."""

    target: Target
    inputs: InputResolutionResult | None = None
    error: str = ""
    failure: RecipeError | None = None


def resolve_targets(
    recipe: Recipe,
    targets: list[Target],
    *,
    inputs: dict[str, object],
    input_from: dict[str, str] | None = None,
    prompt: PromptFunc | None = None,
) -> list[ResolvedTarget]:
    """Resolve every target's inputs serially, in target order, before any planning.

    Prompts (global inputs first, then each target's) therefore never race
    each other or a progress display, and a cancelled prompt propagates to
    abort the whole run. A resolution failure (``ValueError``) only fails its
    own target.
    """
    config = prepare_input_resolution(
        recipe, fixed_values=inputs, input_from=input_from or {}, prompt=prompt
    )
    global_values = resolve_global_values(recipe, config)
    resolved: list[ResolvedTarget] = []
    for target in dedupe_targets(targets):
        try:
            result = resolve_target_inputs(
                recipe, target, config=config, global_values=global_values
            )
        except ValueError as exc:
            resolved.append(ResolvedTarget(target=target, error=str(exc), failure=_failure(exc)))
        else:
            resolved.append(ResolvedTarget(target=target, inputs=result))
    return resolved


class RunBulkApply:
    """Plan a recipe across many target directories."""

    def __init__(self, planner: ApplyRecipe) -> None:
        self._planner = planner

    def plan(
        self,
        *,
        recipe: Recipe,
        recipe_dir: Path,
        local_hook_project: Path | None,
        targets: list[Target],
        inputs: dict[str, object],
        input_from: dict[str, str] | None = None,
        prompt: PromptFunc | None = None,
        parallel: int = 1,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> list[TargetPlan]:
        """Resolve inputs (see :func:`resolve_targets`), then plan every target."""
        return self.plan_resolved(
            recipe=recipe,
            recipe_dir=recipe_dir,
            local_hook_project=local_hook_project,
            resolved=resolve_targets(
                recipe, targets, inputs=inputs, input_from=input_from, prompt=prompt
            ),
            parallel=parallel,
            on_progress=on_progress,
        )

    def plan_resolved(
        self,
        *,
        recipe: Recipe,
        recipe_dir: Path,
        local_hook_project: Path | None,
        resolved: list[ResolvedTarget],
        parallel: int = 1,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> list[TargetPlan]:
        """Return a plan or error row for every resolved target, planning in parallel."""
        plans: dict[int, TargetPlan] = {}

        def record(index: int, plan: TargetPlan) -> None:
            plans[index] = plan
            if on_progress is not None:
                on_progress(len(plans), len(resolved))

        bounded_map(
            lambda index: self._plan_one(recipe, recipe_dir, local_hook_project, resolved[index]),
            range(len(resolved)),
            concurrency=max(1, parallel),
            on_each=record,
        )
        return [plans[index] for index in range(len(resolved))]

    def _plan_one(
        self,
        recipe: Recipe,
        recipe_dir: Path,
        local_hook_project: Path | None,
        item: ResolvedTarget,
    ) -> TargetPlan:
        target = item.target
        if item.inputs is None:
            return TargetPlan(
                target=target.path, status="error", error=item.error, failure=item.failure
            )
        try:
            plan = self._planner(
                recipe=recipe,
                recipe_dir=recipe_dir,
                local_hook_project=local_hook_project,
                target=target.path,
                inputs=item.inputs.values,
            )
            return _suppress_sensitive_diagnostics(
                recipe,
                plan.model_copy(update={"display_inputs": item.inputs.display_values}),
            )
        except Exception as exc:
            display_inputs = item.inputs.display_values
            sensitive = has_sensitive_inputs(recipe.inputs, display_inputs)
            error = SENSITIVE_ERROR_SUPPRESSED if sensitive else str(exc)
            return TargetPlan(
                target=target.path,
                status="error",
                error=error,
                failure=_failure(exc, message=error if sensitive else None),
                display_inputs=display_inputs,
            )


def _failure(exc: Exception, *, message: str | None = None) -> RecipeError:
    """The error behind a failed target, keeping a typed error's category.

    A plain exception is invalid local input (like ``report_config_errors``
    treats one); ``message`` replaces the text (a suppressed diagnostic).
    """
    if isinstance(exc, RecipeError) and message is None:
        return exc
    return RecipeError(message or str(exc), **attribution(exc))


def _suppress_sensitive_diagnostics(
    recipe: Recipe,
    plan: TargetPlan,
) -> TargetPlan:
    if not has_sensitive_inputs(recipe.inputs, plan.display_inputs):
        return plan
    return plan.model_copy(
        update={
            "error": SENSITIVE_ERROR_SUPPRESSED if plan.error else "",
            "warnings": ((SENSITIVE_DIAGNOSTIC_SUPPRESSED,) if plan.warnings else ()),
        }
    )
