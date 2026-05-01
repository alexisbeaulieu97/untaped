"""Render ApplyOutcome diffs and result tables for CLI output."""

from __future__ import annotations

from typing import Any

from untaped_awx.domain import ApplyOutcome, FieldChange


def outcome_rows(outcomes: list[ApplyOutcome]) -> list[dict[str, Any]]:
    """Tabular summary rows for ``--format table`` / ``--format raw``."""
    rows: list[dict[str, Any]] = []
    for o in outcomes:
        rows.append(
            {
                "kind": o.kind,
                "name": o.name,
                "action": o.action,
                "fields_changed": ",".join(_changed_fields(o.changes)),
                "preserved_secrets": ",".join(o.preserved_secrets),
                "detail": o.detail or "",
            }
        )
    return rows


def diff_lines(outcome: ApplyOutcome) -> list[str]:
    """Pretty per-resource diff for stderr (used in preview mode)."""
    if not outcome.changes:
        return [f"{outcome.kind}/{outcome.name}: no changes"]
    out = [f"{outcome.kind}/{outcome.name}:"]
    for change in outcome.changes:
        out.append(f"  {_format_change(change)}")
    return out


def _changed_fields(changes: list[FieldChange]) -> list[str]:
    return [c.field for c in changes if c.note != "preserved existing secret"]


def _format_change(c: FieldChange) -> str:
    if c.note == "preserved existing secret":
        return f"{c.field}: (preserved existing secret)"
    return f"{c.field}: {_short(c.before)} → {_short(c.after)}"


def _short(value: Any, max_len: int = 60) -> str:
    text = repr(value) if value is not None else "—"
    if len(text) > max_len:
        return text[: max_len - 1] + "…"
    return text
