"""Output formatting for stdout — keeps streams pipe-friendly.

Conventions:
- ``json`` and ``yaml`` produce structured output suitable for downstream parsing.
- ``raw`` produces newline-separated rows with tab-separated columns; this is the
  format you pipe into ``fzf``, ``cut``, or ``awk``.
- ``table`` produces a rich-rendered ASCII table for human consumption.
  Its width follows the ``COLUMNS`` env var (or the inherited TTY size);
  no hard-coded cap. Tests that need a stable render can pin ``COLUMNS``.

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
    parsed = [(c, c.split(".")) for c in columns] if columns else None

    if fmt == "raw":
        return _format_raw(rows, parsed)

    selected = (
        [{name: _resolve_path(row, segments) for name, segments in parsed} for row in rows]
        if parsed
        else list(rows)
    )

    if fmt == "json":
        return json.dumps(selected, default=str)
    if fmt == "yaml":
        return yaml.safe_dump(selected, sort_keys=False, default_flow_style=False).rstrip()
    if fmt == "table":
        return _format_table(selected)

    raise ValueError(f"unknown format: {fmt!r}")


def _format_raw(rows: Sequence[Row], parsed: list[tuple[str, list[str]]] | None) -> str:
    if not rows:
        return ""
    if parsed is None:
        first_key = next(iter(rows[0]))
        return "\n".join(_render_cell(row.get(first_key, "")) for row in rows)
    return "\n".join(
        "\t".join(_render_cell(_resolve_path(row, segments)) for _, segments in parsed)
        for row in rows
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
    Console(file=buf, force_terminal=False).print(table)
    return buf.getvalue().rstrip()


def _resolve_path(row: Row, segments: list[str]) -> Any:
    value: Any = row
    for key in segments:
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
