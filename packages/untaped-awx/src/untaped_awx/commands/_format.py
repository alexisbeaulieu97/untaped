"""Render ApplyOutcome diffs and result tables for CLI output."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from untaped_awx.application.resources.apply_field_diff import PRESERVED_SECRET_NOTE
from untaped_awx.domain import ApplyOutcome, FieldChange, IdentityRef
from untaped_awx.records import ApplyOutcomeRow


def outcome_rows(outcomes: list[ApplyOutcome]) -> list[ApplyOutcomeRow]:
    """Summary rows for every output format; a failed row keeps its ``error``."""
    return [
        ApplyOutcomeRow(
            id=o.id,
            kind=o.kind,
            scope=o.scope,
            identity=o.identity,
            partial=o.partial,
            unverified=o.unverified,
            name=o.name,
            action=o.action,
            fields_changed=_changed_fields(o.changes),
            preserved_secrets=list(o.preserved_secrets),
            dropped_undeclared_secrets=list(o.dropped_undeclared_secrets),
            dropped_secrets=list(o.dropped_secrets),
            detail=o.detail or "",
            error=o.error,
        )
        for o in outcomes
    ]


_SCOPE_LABELS = {"organization": "org", "inventory__organization": "inventory_org"}


def format_scope(scope: Mapping[str, Any] | None) -> str:
    """Render a selection scope as ``org=Default inventory=prod`` (``-`` if empty)."""
    if not scope:
        return "-"
    return " ".join(
        f"{_SCOPE_LABELS.get(key, key)}={_scope_value(value)}" for key, value in scope.items()
    )


def _scope_value(value: Any) -> str:
    """A scope value; a parent reference reads ``Inventory/prod (org Default)``."""
    ref = value.model_dump() if isinstance(value, IdentityRef) else value
    if not isinstance(ref, Mapping) or not {"kind", "name"} <= ref.keys():
        return str(value)
    text = f"{ref['kind']}/{ref['name']}"
    if ref.get("organization"):
        text += f" (org {ref['organization']})"
    if ref.get("parent"):
        text += f" in {_scope_value(ref['parent'])}"
    return text


def format_value(value: Any) -> str:
    """Render a preview value as compact JSON (strings quoted, ``null`` for None)."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, default=str)


def _changed_fields(changes: list[FieldChange]) -> list[str]:
    return [c.field for c in changes if c.note != PRESERVED_SECRET_NOTE]
