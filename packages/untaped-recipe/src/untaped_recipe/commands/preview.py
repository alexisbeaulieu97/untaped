"""``recipe apply``'s preview: what a person reads on stderr before the question.

Lines and tables of rows, which core prints on stderr in the run's theme: the
same whatever ``--format`` asks of stdout, and kept under ``--quiet``.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel

from untaped.sdk import Column, PreviewItem, plural, unified_diff_text
from untaped_recipe.application.inputs import has_sensitive_inputs
from untaped_recipe.domain.plan import FileChange, TargetPlan, is_binary_content
from untaped_recipe.domain.recipe import RecipeFile

PreviewMode = Literal["table", "diff", "none"]


class ChangeRow(BaseModel):
    """One file a target's change writes."""

    path: Annotated[str, Column]
    action: Annotated[str, Column]
    changes: Annotated[str, Column]


class TargetRow(BaseModel):
    """One target's changes, summed, when its files are too many to list."""

    target: Annotated[str, Column]
    files: Annotated[int, Column]
    changes: Annotated[str, Column]


class HiddenRow(BaseModel):
    """A target whose changes are not shown, because its inputs are sensitive."""

    target: Annotated[str, Column]
    files_changed: Annotated[int, Column]


class ErrorRow(BaseModel):
    """A target that failed planning, and why (``detail`` wraps, never cut)."""

    target: Annotated[str, Column]
    detail: Annotated[str | None, Column]


def preview_items(
    recipe: RecipeFile,
    plans: list[TargetPlan],
    *,
    preview: PreviewMode,
    preview_max_rows: int = 50,
) -> list[PreviewItem]:
    """The selected preview of the planned targets, after their summary line."""
    items: list[PreviewItem] = [preview_summary(plans)]
    if preview == "diff":
        items.extend(_diff_preview(recipe, plans))
    elif preview == "table":
        items.extend(_table_preview(recipe, plans, preview_max_rows=preview_max_rows))
    return [item for item in items if isinstance(item, str) or item]


@dataclass(frozen=True)
class PlanCounts:
    """Per-status target counts shared by the preview and result summaries."""

    total: int
    failed: int
    skipped: int
    changing: int
    unchanged: int
    files_changed: int

    @classmethod
    def of(cls, plans: list[TargetPlan]) -> PlanCounts:
        """Count planned targets; errors and skips are neither changing nor unchanged."""
        settled = [plan for plan in plans if plan.status not in {"error", "skipped"}]
        return cls(
            total=len(plans),
            failed=sum(1 for plan in plans if plan.status == "error"),
            skipped=sum(1 for plan in plans if plan.status == "skipped"),
            changing=sum(1 for plan in settled if plan.changes),
            unchanged=sum(1 for plan in settled if not plan.changes),
            files_changed=sum(plan.files_changed for plan in settled),
        )


def preview_summary(plans: list[TargetPlan]) -> str:
    """Render the pre-run aggregate preview summary."""
    counts = PlanCounts.of(plans)
    skipped_note = f"{counts.skipped} skipped, " if counts.skipped else ""
    return (
        "Recipe preview: "
        f"{plural(counts.total, 'target')}, "
        f"{counts.changing} changing, "
        f"{counts.unchanged} unchanged, "
        f"{skipped_note}"
        f"{counts.failed} failed, "
        f"{plural(counts.files_changed, 'file')} changed"
    )


def _diff_preview(recipe: RecipeFile, plans: list[TargetPlan]) -> list[PreviewItem]:
    diffable_plans, hidden_rows, error_rows = _preview_groups(recipe, plans)
    lines: list[PreviewItem] = []
    for plan in diffable_plans:
        target = _display_target(plan)
        for change in plan.changes:
            path = change.relative_path.as_posix()
            if is_binary_content(change.before) or is_binary_content(change.after):
                diff = f"Binary file {path} differs\n"
            else:
                diff = unified_diff_text(change.before, change.after, path=path)
            if diff:
                lines.append(f"# {target}")
                lines.append(diff.removesuffix("\n"))
    lines.extend([hidden_rows, error_rows])
    return lines


def _table_preview(
    recipe: RecipeFile,
    plans: list[TargetPlan],
    *,
    preview_max_rows: int,
) -> list[PreviewItem]:
    diffable_plans, hidden_rows, error_rows = _preview_groups(recipe, plans)
    lines: list[PreviewItem] = []
    total_rows = sum(len(plan.changes) for plan in diffable_plans)
    if preview_max_rows == 0 or total_rows <= preview_max_rows:
        lines.append(
            [
                ChangeRow(
                    path=str(_display_change_path(change)),
                    action=change.kind,
                    changes=_change_counts(change),
                )
                for plan in diffable_plans
                for change in plan.changes
            ]
        )
    else:
        target_rows = [_target_summary_row(plan) for plan in diffable_plans]
        lines.append(target_rows[:preview_max_rows])
        if len(target_rows) > preview_max_rows:
            lines.append(
                "showing first "
                f"{preview_max_rows} of {len(target_rows)} targets "
                "(use --preview diff for full detail)"
            )
    lines.extend([hidden_rows, error_rows])
    return lines


def _preview_groups(
    recipe: RecipeFile,
    plans: list[TargetPlan],
) -> tuple[list[TargetPlan], list[HiddenRow], list[ErrorRow]]:
    diffable_plans: list[TargetPlan] = []
    hidden_rows: list[HiddenRow] = []
    error_rows: list[ErrorRow] = []
    for plan in plans:
        if plan.status == "error":
            error_rows.append(ErrorRow(target=str(_display_target(plan)), detail=plan.error))
            continue
        if not plan.changes:
            continue
        if has_sensitive_inputs(recipe.inputs, plan.display_inputs):
            hidden_rows.append(
                HiddenRow(target=str(_display_target(plan)), files_changed=plan.files_changed)
            )
            continue
        diffable_plans.append(plan)
    return diffable_plans, hidden_rows, error_rows


def _display_change_path(change: FileChange) -> Path:
    return _absolute_display_path(change.target / change.relative_path)


def _display_target(plan: TargetPlan) -> Path:
    return _absolute_display_path(plan.target)


def _absolute_display_path(path: Path) -> Path:
    if path.is_absolute():
        return path
    return Path.cwd() / path


def _target_summary_row(plan: TargetPlan) -> TargetRow:
    additions = 0
    deletions = 0
    for change in plan.changes:
        change_additions, change_deletions = _count_change_lines(change)
        additions += change_additions
        deletions += change_deletions
    return TargetRow(
        target=str(_display_target(plan)),
        files=plan.files_changed,
        changes=f"+{additions} -{deletions}",
    )


def _change_counts(change: FileChange) -> str:
    additions, deletions = _count_change_lines(change)
    return f"+{additions} -{deletions}"


def _count_change_lines(change: FileChange) -> tuple[int, int]:
    before = [] if change.before is None else change.before.splitlines(keepends=True)
    after = [] if change.after is None else change.after.splitlines(keepends=True)
    additions = 0
    deletions = 0
    matcher = difflib.SequenceMatcher(a=before, b=after, autojunk=False)
    for tag, before_start, before_end, after_start, after_end in matcher.get_opcodes():
        if tag == "replace":
            deletions += before_end - before_start
            additions += after_end - after_start
        elif tag == "delete":
            deletions += before_end - before_start
        elif tag == "insert":
            additions += after_end - after_start
    return additions, deletions
