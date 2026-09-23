"""Render ApplyOutcome diffs and result tables for CLI output."""

import json
from collections.abc import Mapping
from typing import Any

from untaped.capabilities.awx.application.apply_field_diff import PRESERVED_SECRET_NOTE
from untaped.capabilities.awx.domain import ApplyOutcome, FieldChange


def outcome_rows(outcomes: list[ApplyOutcome]) -> list[dict[str, Any]]:
    """Tabular summary rows for ``--format table`` / ``--format raw``."""
    rows: list[dict[str, Any]] = []
    for o in outcomes:
        data = o.model_dump(mode="json")
        rows.append(
            {
                "id": o.id,
                "kind": o.kind,
                "scope": data["scope"],
                "identity": data["identity"],
                "partial": o.partial,
                "unverified": o.unverified,
                "name": o.name,
                "action": o.action,
                "fields_changed": ",".join(_changed_fields(o.changes)),
                "preserved_secrets": ",".join(o.preserved_secrets),
                "detail": o.detail or "",
            }
        )
    return rows


_SCOPE_LABELS = {"organization": "org", "inventory__organization": "inventory_org"}


def format_scope(scope: Mapping[str, Any] | None) -> str:
    """Render a selection scope as ``org=Default inventory=prod`` (``-`` if empty)."""
    if not scope:
        return "-"
    return " ".join(f"{_SCOPE_LABELS.get(key, key)}={value}" for key, value in scope.items())


def format_value(value: Any) -> str:
    """Render a preview value as compact JSON (strings quoted, ``null`` for None)."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, default=str)


def diff_lines(outcome: ApplyOutcome) -> list[str]:
    """Pretty per-resource diff for stderr (used in preview mode)."""
    if not outcome.changes:
        return [f"{outcome.kind}/{outcome.name}: no changes"]
    out = [f"{outcome.kind}/{outcome.name}:"]
    for change in outcome.changes:
        out.append(f"  {_format_change(change)}")
    return out


def _changed_fields(changes: list[FieldChange]) -> list[str]:
    return [c.field for c in changes if c.note != PRESERVED_SECRET_NOTE]


def _format_change(c: FieldChange) -> str:
    if c.note == PRESERVED_SECRET_NOTE:
        return f"{c.field}: ({PRESERVED_SECRET_NOTE})"
    return f"{c.field}: {_short(c.before)} → {_short(c.after)}"


def _short(value: Any, max_len: int = 60) -> str:
    text = repr(value) if value is not None else "—"
    if len(text) > max_len:
        return text[: max_len - 1] + "…"
    return text
