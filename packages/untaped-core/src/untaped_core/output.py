"""Output formatting for stdout — keeps streams pipe-friendly.

Conventions:
- ``json`` and ``yaml`` produce structured output suitable for downstream parsing.
- ``raw`` produces newline-separated rows with tab-separated columns; this is the
  format you pipe into ``fzf``, ``cut``, or ``awk``.
- ``table`` produces a rich-rendered ASCII table for human consumption.

If no ``columns`` are specified for ``raw``, the first key of each row is used.
"""

from __future__ import annotations

import io
import json
from collections.abc import Sequence
from typing import Literal

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
    return [{c: row.get(c) for c in columns} for row in rows]


def _format_raw(rows: Sequence[Row], columns: list[str] | None) -> str:
    if not rows:
        return ""
    if columns is None:
        first_key = next(iter(rows[0]))
        return "\n".join(str(row.get(first_key, "")) for row in rows)
    return "\n".join("\t".join(str(row.get(c, "")) for c in columns) for row in rows)


def _format_table(rows: Sequence[Row]) -> str:
    if not rows:
        return ""
    table = Table(show_header=True, header_style="bold", box=box.ROUNDED)
    columns = list(rows[0].keys())
    for col in columns:
        table.add_column(col)
    for row in rows:
        table.add_row(*[str(row.get(c, "")) for c in columns])
    buf = io.StringIO()
    Console(file=buf, force_terminal=False, width=120).print(table)
    return buf.getvalue().rstrip()
