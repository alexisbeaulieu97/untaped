"""Replace FK ids in result rows with names from ``summary_fields``."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from untaped.capabilities.awx.infrastructure.spec import AwxResourceSpec


def name_fks(
    rows: list[dict[str, Any]],
    spec: AwxResourceSpec,
    *,
    with_names: bool,
    table: bool,
    columns: Sequence[str] | None,
    defaults: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """``rows`` with FK names where they are wanted: always in a ``table``, else ``with_names``.

    Display-only FK columns are those ``columns`` mentions (``+``/``-``
    edits included) plus ``defaults``, the columns the view shows unasked.
    Names come from each row's ``summary_fields``, so this reads nothing.
    """
    if not (with_names or table):
        return rows
    return flatten_fks(rows, spec, columns=[*defaults, *column_names(columns)])


def column_names(columns: Sequence[str] | None) -> list[str]:
    """Every column name ``--columns`` mentions, ``+``/``-`` edit marks stripped.

    Follows the core's ``--columns`` syntax (``-c a,b``, ``-c +a``,
    ``-c=-a``; see ``_selected_columns`` in ``untaped.cli``), which
    ``capability_api`` does not expose as a parser.
    """
    parts = (part.strip() for entry in columns or () for part in entry.split(","))
    return [part.lstrip("+-") for part in parts if part]


def flatten_fks(
    rows: Iterable[dict[str, Any]],
    spec: AwxResourceSpec,
    *,
    columns: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return a copy of ``rows`` with each FK id replaced by its server-resolved name.

    Two passes:

    1. **Declared FKs** — every entry in ``spec.fk_refs`` is flattened
       (single or multi). Multi FKs (``credentials = [30, 31]``) become
       lists of names.
    2. **Display-only FK columns** — when ``columns`` is provided, any
       column whose name matches a top-level key with a corresponding
       ``summary_fields.<col>.name`` is also flattened, even if the
       column is not in ``spec.fk_refs``. This catches FK fields that
       live in ``read_only_fields`` because their identity comes from
       ``metadata.parent`` rather than the spec body — the canonical
       case is Host's / Group's ``inventory``.

    A missing ``summary_fields`` entry falls back to the original id
    so bad/partial server responses don't disappear mid-pipeline. Only
    rows are copied at the top level — nested values are shared with
    the input, so don't reuse a row dict you intend to mutate.

    Dotted column names (``summary_fields.inventory.name``) are
    deliberately excluded from extra-column flattening: the user is
    already addressing nested data directly via the row renderer's
    dotted-path walker, so flattening would double-resolve.
    """
    declared_fk_fields = {fk.field for fk in spec.fk_refs if not fk.polymorphic}
    extra_cols = (
        [c for c in columns if c not in declared_fk_fields and "." not in c] if columns else []
    )
    return [_flatten_one(row, spec, extra_cols=extra_cols) for row in rows]


def _flatten_one(
    row: dict[str, Any],
    spec: AwxResourceSpec,
    *,
    extra_cols: list[str],
) -> dict[str, Any]:
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
    # Display-only pass: flatten any caller-supplied column whose
    # ``summary_fields.<col>.name`` exists but isn't an fk_refs entry.
    # Catches Host's / Group's ``inventory`` (lives in read_only_fields,
    # FK identity comes from metadata.parent).
    for col in extra_cols:
        sf_entry = summary.get(col)
        if isinstance(sf_entry, dict) and "name" in sf_entry and col in new_row:
            new_row[col] = _name_or_id(sf_entry, new_row[col])
    return new_row


def _name_or_id(summary_entry: Any, fallback: Any) -> Any:
    if isinstance(summary_entry, dict) and "name" in summary_entry:
        return summary_entry["name"]
    return fallback
