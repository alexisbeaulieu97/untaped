"""Render ApplyOutcome diffs and result tables for CLI output."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from untaped_awx.application.apply_field_diff import PRESERVED_SECRET_NOTE
from untaped_awx.domain import ApplyOutcome, FieldChange

OUTCOME_TABLE_COLUMNS = ("id", "name", "kind", "action", "fields_changed", "detail")
"""A mutation table's defaults; ``scope``, ``identity`` and the flags stay in the record."""


def outcome_rows(outcomes: list[ApplyOutcome]) -> list[dict[str, Any]]:
    """Summary rows for every output format; a failed row keeps its ``error``."""
    rows: list[dict[str, Any]] = []
    for o in outcomes:
        data = o.model_dump(mode="json")
        error = {"error": data["error"]} if "error" in data else {}
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
                "fields_changed": _changed_fields(o.changes),
                "preserved_secrets": list(o.preserved_secrets),
                "dropped_undeclared_secrets": list(o.dropped_undeclared_secrets),
                "dropped_secrets": list(o.dropped_secrets),
                "detail": o.detail or "",
                **error,
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


def _changed_fields(changes: list[FieldChange]) -> list[str]:
    return [c.field for c in changes if c.note != PRESERVED_SECRET_NOTE]
