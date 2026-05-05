"""Output formatting for stdout — keeps streams pipe-friendly.

Conventions:
- ``json`` and ``yaml`` produce structured output suitable for downstream parsing.
- ``raw`` produces newline-separated rows with tab-separated columns; this is the
  format you pipe into ``fzf``, ``cut``, or ``awk``.
- ``table`` produces a rich-rendered ASCII table for human consumption.

If no ``columns`` are specified for ``raw``, the first key of each row is used.

Column names support dotted paths (``a.b.c``) to address nested dict
fields — e.g. ``--columns summary_fields.project.name``. Missing
intermediates resolve to ``None`` rather than erroring so a column
specification works uniformly across heterogeneous rows.
"""

from __future__ import annotations

import io
import json
from collections.abc import Sequence
from typing import Any, Literal

import yaml
from rich import box
from rich.console import Console
from rich.table import Table

OutputFormat = Literal["json", "yaml", "table", "raw"]

Row = dict[str, object]


def format_output(
    rows: Sequence[Row],
    *,
    fmt: OutputFormat,
    columns: list[str] | None = None,
) -> str:
    """Render ``rows`` as a string in the requested format."""
    selected = _select_columns(rows, columns) if columns else list(rows)

    if fmt == "json":
        return json.dumps(selected, default=str)
    if fmt == "yaml":
        return yaml.safe_dump(selected, sort_keys=False, default_flow_style=False).rstrip()
    if fmt == "raw":
        return _format_raw(rows, columns)
    if fmt == "table":
        return _format_table(selected)

    raise ValueError(f"unknown format: {fmt!r}")


def _select_columns(rows: Sequence[Row], columns: list[str]) -> list[Row]:
    return [{c: _resolve_path(row, c) for c in columns} for row in rows]


def _format_raw(rows: Sequence[Row], columns: list[str] | None) -> str:
    if not rows:
        return ""
    if columns is None:
        first_key = next(iter(rows[0]))
        return "\n".join(_render_cell(row.get(first_key, "")) for row in rows)
    return "\n".join(
        "\t".join(_render_cell(_resolve_path(row, c)) for c in columns) for row in rows
    )


def _format_table(rows: Sequence[Row]) -> str:
    if not rows:
        return ""
    table = Table(show_header=True, header_style="bold", box=box.ROUNDED)
    columns = list(rows[0].keys())
    for col in columns:
        table.add_column(col)
    for row in rows:
        table.add_row(*[_render_cell(row.get(c, "")) for c in columns])
    buf = io.StringIO()
    Console(file=buf, force_terminal=False, width=120).print(table)
    return buf.getvalue().rstrip()


def _resolve_path(row: Row, path: str) -> Any:
    """Resolve a dotted column path against ``row``.

    A leading literal lookup is attempted first so legitimate keys that
    happen to contain dots still match (none today, but the preservation
    is cheap). Falls through to dotted traversal — each segment indexes
    into the previous dict; non-dict intermediates short-circuit to
    ``None``.
    """
    if path in row:
        return row[path]
    value: Any = row
    for key in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _render_cell(value: Any) -> str:
    """Stringify a cell value with one tweak for the common multi-FK case.

    AWX returns multi-valued FKs (``credentials``) as JSON-style lists
    (``[30, 31]``). ``str([30, 31])`` is fine, but lists of names after
    ``--with-names`` ("[ssh, vault]") look prettier as ``ssh, vault``.
    Apply that flatten only for shallow scalar lists; nested structures
    fall back to ``str(...)`` so structured data stays inspectable.
    """
    if isinstance(value, list) and all(_is_scalar(v) for v in value):
        return ", ".join("" if v is None else str(v) for v in value)
    return "" if value is None else str(value)


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, str | int | float | bool)
