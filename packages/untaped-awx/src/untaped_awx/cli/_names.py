"""Replace FK ids in result rows with names from ``summary_fields``."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

    from untaped_awx.infrastructure.spec import AwxResourceSpec


def flatten_fks(rows: Iterable[dict[str, Any]], spec: AwxResourceSpec) -> list[dict[str, Any]]:
    """Return a copy of ``rows`` with each FK id replaced by its server-resolved name.

    Multi FKs (``credentials = [30, 31]``) become lists of names. A
    missing ``summary_fields`` entry falls back to the original id so
    bad/partial server responses don't disappear mid-pipeline. Only
    rows are copied at the top level — nested values are shared with
    the input, so don't reuse a row dict you intend to mutate.
    """
    return [_flatten_one(row, spec) for row in rows]


def _flatten_one(row: dict[str, Any], spec: AwxResourceSpec) -> dict[str, Any]:
    summary = row.get("summary_fields") or {}
    new_row = dict(row)
    for fk in spec.fk_refs:
        # Polymorphic FKs (Schedule's "parent") live under a different
        # wire key than the spec's logical name; users wanting the
        # parent's name reach for a dotted column instead.
        if fk.polymorphic:
            continue
        value = new_row.get(fk.field)
        sf_entry = summary.get(fk.field)
        if value is None or sf_entry is None:
            continue
        if fk.multi:
            # Walk the id list and look up each summary entry by index.
            # AWX sometimes returns a shorter `summary_fields` list than
            # the raw id list (degraded response); we must preserve the
            # original cardinality so callers don't lose ids silently.
            if isinstance(value, list):
                summary_list = sf_entry if isinstance(sf_entry, list) else []
                new_row[fk.field] = [
                    _name_or_id(summary_list[i] if i < len(summary_list) else None, v)
                    for i, v in enumerate(value)
                ]
        else:
            new_row[fk.field] = _name_or_id(sf_entry, value)
    return new_row


def _name_or_id(summary_entry: Any, fallback: Any) -> Any:
    if isinstance(summary_entry, dict) and "name" in summary_entry:
        return summary_entry["name"]
    return fallback
