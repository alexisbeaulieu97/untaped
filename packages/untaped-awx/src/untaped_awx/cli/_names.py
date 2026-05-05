"""Translate FK ids in result rows to human names via ``summary_fields``.

AWX returns FK columns as numeric ids (``project: 10``) plus a parallel
``summary_fields`` map (``summary_fields.project.name = "playbooks"``)
populated by the server. The ``--with-names`` flag flips every FK
column declared in the spec to its name in-place — turning a wall of
ids into something readable in ``--format table`` and pipe-friendly
when grepping.

Polymorphic FKs (Schedule's ``parent``) live under a different wire key
than the spec's logical name and aren't translated here; users wanting
the parent's name should reach for the dotted-column accessor
instead, e.g. ``--columns summary_fields.unified_job_template.name``.
"""

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
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(_flatten_one(row, spec))
    return out


def _flatten_one(row: dict[str, Any], spec: AwxResourceSpec) -> dict[str, Any]:
    summary = row.get("summary_fields") or {}
    new_row = dict(row)
    for fk in spec.fk_refs:
        if fk.polymorphic:
            # The wire key for a polymorphic FK isn't the FK's logical
            # field name (Schedule's "parent" lives under
            # "unified_job_template" on the wire); --with-names skips
            # these and defers to dotted columns.
            continue
        value = new_row.get(fk.field)
        if value is None:
            continue
        sf_entry = summary.get(fk.field)
        if sf_entry is None:
            continue
        if fk.multi:
            if not isinstance(value, list) or not isinstance(sf_entry, list):
                continue
            new_row[fk.field] = [_name_or_id(s, v) for s, v in zip(sf_entry, value, strict=False)]
        else:
            if isinstance(sf_entry, dict) and "name" in sf_entry:
                new_row[fk.field] = sf_entry["name"]
    return new_row


def _name_or_id(summary_entry: Any, fallback: Any) -> Any:
    if isinstance(summary_entry, dict) and "name" in summary_entry:
        return summary_entry["name"]
    return fallback
