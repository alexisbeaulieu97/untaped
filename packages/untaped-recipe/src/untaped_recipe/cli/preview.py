"""Human preview rendering for recipe apply."""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from untaped.sdk import Diagnostics, plural, ui_context, unified_diff_text
from untaped_recipe.application.inputs import has_sensitive_inputs
from untaped_recipe.domain.plan import FileChange, TargetPlan, is_binary_content
from untaped_recipe.domain.recipe import Recipe

PreviewMode = Literal["table", "diff", "none"]

_diag = Diagnostics()


def render_preview(
    recipe: Recipe,
    plans: list[TargetPlan],
    *,
    preview: PreviewMode,
    preview_max_rows: int = 50,
) -> None:
    """Render the selected stderr preview for planned targets."""
    _diag.info(preview_summary(plans), unmuted=True)
    if preview == "none":
        return
    if preview == "diff":
        _render_diff_preview(recipe, plans)
        return
    _render_table_preview(recipe, plans, preview_max_rows=preview_max_rows)


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


def _render_diff_preview(recipe: Recipe, plans: list[TargetPlan]) -> None:
    diffable_plans, suppressed_rows, error_rows = _preview_groups(recipe, plans)
    for plan in diffable_plans:
        target = _display_target(plan)
        for change in plan.changes:
            path = change.relative_path.as_posix()
            if is_binary_content(change.before) or is_binary_content(change.after):
                diff = f"Binary file {path} differs\n"
            else:
                diff = unified_diff_text(change.before, change.after, path=path)
            if diff:
                _diag.info(f"# {target}", unmuted=True)
                _diag.info(diff.removesuffix("\n"), unmuted=True)
    _render_stderr_table(suppressed_rows, columns=["target", "files_changed"])
    _render_stderr_table(error_rows, columns=["target", "error"])


def _render_table_preview(
    recipe: Recipe,
    plans: list[TargetPlan],
    *,
    preview_max_rows: int,
) -> None:
    diffable_plans, suppressed_rows, error_rows = _preview_groups(recipe, plans)
    total_rows = sum(len(plan.changes) for plan in diffable_plans)
    if preview_max_rows == 0 or total_rows <= preview_max_rows:
        normal_rows: list[dict[str, object]] = []
        for plan in diffable_plans:
            normal_rows.extend(
                {
                    "path": str(_display_change_path(change)),
                    "action": change.kind,
                    "changes": _change_counts(change),
                }
                for change in plan.changes
            )
        _render_stderr_table(normal_rows, columns=["path", "action", "changes"])
    else:
        target_rows = [_target_summary_row(plan) for plan in diffable_plans]
        rendered_rows = target_rows
        if len(target_rows) > preview_max_rows:
            rendered_rows = target_rows[:preview_max_rows]
        _render_stderr_table(rendered_rows, columns=["target", "files", "changes"])
        if len(target_rows) > preview_max_rows:
            _diag.info(
                "showing first "
                f"{preview_max_rows} of {len(target_rows)} targets "
                "(use --preview diff for full detail)",
                unmuted=True,
            )
    _render_stderr_table(suppressed_rows, columns=["target", "files_changed"])
    _render_stderr_table(error_rows, columns=["target", "error"])


def _preview_groups(
    recipe: Recipe,
    plans: list[TargetPlan],
) -> tuple[list[TargetPlan], list[dict[str, object]], list[dict[str, object]]]:
    diffable_plans: list[TargetPlan] = []
    suppressed_rows: list[dict[str, object]] = []
    error_rows: list[dict[str, object]] = []
    for plan in plans:
        if plan.status == "error":
            error_rows.append({"target": str(_display_target(plan)), "error": plan.error})
            continue
        if not plan.changes:
            continue
        if has_sensitive_inputs(recipe.inputs, plan.display_inputs):
            suppressed_rows.append(
                {
                    "target": str(_display_target(plan)),
                    "files_changed": plan.files_changed,
                }
            )
            continue
        diffable_plans.append(plan)
    return diffable_plans, suppressed_rows, error_rows


def _render_stderr_table(rows: list[dict[str, object]], *, columns: list[str]) -> None:
    """Print ``rows`` to stderr as aligned columns, or ``key: value`` lines.

    A preview is text a person reads before the question, not records, so it
    is the same whatever ``--format`` asks of stdout, and ``--quiet`` keeps
    it. With ``untaped.ui.hide_empty_columns`` (the default) a column empty on every
    row is left out; ``untaped.ui.collection_view: list`` shows each row as
    ``key: value`` lines.
    """
    if not rows:
        return
    theme = ui_context(strict=False).theme
    if theme.hide_empty_columns:
        # As in any table, unless every column is empty.
        shown = [column for column in columns if any(_cell(row.get(column)) for row in rows)]
        columns = shown or columns
    cells = [[_cell(row.get(column)) for column in columns] for row in rows]
    if theme.collection_view == "list":
        blocks = (
            "\n".join(f"{name}: {value}" for name, value in zip(columns, line, strict=True))
            for line in cells
        )
        _diag.info("\n\n".join(blocks), unmuted=True)
        return
    lines = [columns, *cells]
    widths = [max(len(line[index]) for line in lines) for index in range(len(columns))]
    text = (
        "  ".join(cell.ljust(width) for cell, width in zip(line, widths, strict=True)).rstrip()
        for line in lines
    )
    _diag.info("\n".join(text), unmuted=True)


def _cell(value: object) -> str:
    return "" if value is None else str(value)


def _display_change_path(change: FileChange) -> Path:
    return _absolute_display_path(change.target / change.relative_path)


def _display_target(plan: TargetPlan) -> Path:
    return _absolute_display_path(plan.target)


def _absolute_display_path(path: Path) -> Path:
    if path.is_absolute():
        return path
    return Path.cwd() / path


def _target_summary_row(plan: TargetPlan) -> dict[str, object]:
    additions = 0
    deletions = 0
    for change in plan.changes:
        change_additions, change_deletions = _count_change_lines(change)
        additions += change_additions
        deletions += change_deletions
    return {
        "target": str(_display_target(plan)),
        "files": plan.files_changed,
        "changes": f"+{additions} -{deletions}",
    }


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
