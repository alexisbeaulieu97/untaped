"""Format rendering: structured/raw/pipe encoders and the Rich terminal renderer.

Split from ``ui.py``: this module owns *what output looks like* (the
``Renderer`` boundary and its default implementation plus the pure format
helpers); ``ui.py`` owns *interaction* (``UiContext`` — prompts, messages,
progress). The two halves share only ``ThemeSpec``.
"""

from __future__ import annotations

import io
import json
import os
import shutil
from collections.abc import Sequence
from typing import Any, Literal, Protocol, TextIO

import yaml
from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

from untaped.pipe import PIPE_ENVELOPE_VERSION, PIPE_MARKER_KEY
from untaped.theme import DEFAULT_SYMBOLS, BorderStyle, ThemeSpec

OutputFormat = Literal["json", "yaml", "table", "raw", "pipe"]
MessageKind = Literal["success", "warning", "error", "info"]

Row = dict[str, object]


class Renderer(Protocol):
    """Renderer boundary for semantic UI primitives."""

    def render_collection(
        self,
        rows: Sequence[Row],
        *,
        fmt: OutputFormat,
        columns: list[str] | None,
        theme: ThemeSpec,
        colorize: bool,
        kind: str | None = None,
    ) -> str: ...

    def render_detail(
        self,
        record: Row,
        *,
        fmt: OutputFormat,
        columns: list[str] | None,
        theme: ThemeSpec,
        colorize: bool,
        kind: str | None = None,
    ) -> str: ...

    def render_message(
        self,
        kind: MessageKind,
        text: str,
        *,
        theme: ThemeSpec,
        colorize: bool,
    ) -> str: ...


class RichTerminalRenderer:
    """Default renderer for human terminal output and structured formats."""

    def render_collection(
        self,
        rows: Sequence[Row],
        *,
        fmt: OutputFormat,
        columns: list[str] | None,
        theme: ThemeSpec,
        colorize: bool,
        kind: str | None = None,
    ) -> str:
        parsed = _parse_columns(columns)
        if fmt == "raw":
            return _format_raw(rows, parsed)
        if fmt == "pipe":
            return _format_pipe(rows, kind)

        selected = _select_rows(rows, parsed)
        if fmt == "json":
            return json.dumps(selected, default=str)
        if fmt == "yaml":
            return yaml.safe_dump(selected, sort_keys=False, default_flow_style=False).rstrip()
        if fmt == "table":
            if theme.collection_view == "list":
                return _format_records_as_lines(selected, theme=theme, colorize=colorize)
            return _format_table(selected, theme, colorize=colorize)

        raise ValueError(f"unknown format: {fmt!r}")

    def render_detail(
        self,
        record: Row,
        *,
        fmt: OutputFormat,
        columns: list[str] | None,
        theme: ThemeSpec,
        colorize: bool,
        kind: str | None = None,
    ) -> str:
        parsed = _parse_columns(columns)
        if fmt == "pipe":
            return _format_pipe([record], kind)
        selected = _select_record(record, parsed)
        if fmt == "raw":
            if not selected:
                return ""
            first_key = next(iter(selected))
            return _render_cell(selected.get(first_key, ""))
        if fmt == "json":
            return json.dumps(selected, default=str)
        if fmt == "yaml":
            return yaml.safe_dump(selected, sort_keys=False, default_flow_style=False).rstrip()
        if fmt == "table":
            if theme.detail_view == "table":
                rows = [{"field": key, "value": value} for key, value in selected.items()]
                return _format_table(rows, theme, colorize=colorize)
            return _format_record_as_lines(selected, theme=theme, colorize=colorize)

        raise ValueError(f"unknown format: {fmt!r}")

    def render_message(
        self,
        kind: MessageKind,
        text: str,
        *,
        theme: ThemeSpec,
        colorize: bool,
    ) -> str:
        symbol = theme.symbols.get(kind, DEFAULT_SYMBOLS[kind])
        prefix = f"{symbol} " if symbol else ""
        label = f"{kind}: " if kind in {"warning", "error"} else ""
        rendered = f"{prefix}{label}{text}"
        style = _role_style(theme, kind, colorize=colorize)
        if style is None:
            return rendered
        return _render_text(Text(rendered, style=style), colorize=colorize)


def _parse_columns(columns: list[str] | None) -> list[tuple[str, list[str]]] | None:
    return [(c, c.split(".")) for c in columns] if columns else None


def _select_rows(rows: Sequence[Row], parsed: list[tuple[str, list[str]]] | None) -> list[Row]:
    if parsed is None:
        return list(rows)
    return [{name: _resolve_path(row, segments) for name, segments in parsed} for row in rows]


def _select_record(record: Row, parsed: list[tuple[str, list[str]]] | None) -> Row:
    if parsed is None:
        return dict(record)
    return {name: _resolve_path(record, segments) for name, segments in parsed}


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


def _format_pipe(rows: Sequence[Row], kind: str | None) -> str:
    """Render rows as the self-describing NDJSON ``pipe`` envelope (full records)."""
    return "\n".join(
        json.dumps(
            {PIPE_MARKER_KEY: PIPE_ENVELOPE_VERSION, "kind": kind, "record": dict(row)},
            default=str,
        )
        for row in rows
    )


def _format_table(rows: Sequence[Row], theme: ThemeSpec, *, colorize: bool) -> str:
    if not rows:
        return ""
    table = Table(
        show_header=True,
        header_style=_role_style(theme, "header", colorize=colorize) or "",
        border_style=_role_style(theme, "border", colorize=colorize),
        box=_resolve_box(theme.border),
        padding=(0, 0) if theme.density == "compact" else (0, 1),
    )
    columns = list(rows[0].keys())
    for col in columns:
        table.add_column(col)
    value_style = _role_style(theme, "value", colorize=colorize)
    for row in rows:
        table.add_row(*[_styled_text(_render_cell(row.get(c, "")), value_style) for c in columns])
    return _render_rich(table, colorize=colorize)


def _format_records_as_lines(
    rows: Sequence[Row],
    *,
    theme: ThemeSpec,
    colorize: bool,
) -> str:
    return "\n\n".join(_format_record_as_lines(row, theme=theme, colorize=colorize) for row in rows)


def _format_record_as_lines(record: Row, *, theme: ThemeSpec, colorize: bool) -> str:
    return "\n".join(
        _format_record_line(key, value, theme=theme, colorize=colorize)
        for key, value in record.items()
    )


def _format_record_line(key: str, value: object, *, theme: ThemeSpec, colorize: bool) -> str:
    key_style = _role_style(theme, "key", colorize=colorize)
    value_style = _role_style(theme, "value", colorize=colorize)
    rendered_value = _render_cell(value)
    if key_style is None and value_style is None:
        return f"{key}: {rendered_value}"
    line = Text()
    line.append(key, style=key_style)
    line.append(": ")
    line.append(rendered_value, style=value_style)
    return _render_text(line, colorize=colorize)


def _resolve_box(border: BorderStyle) -> box.Box | None:
    if border == "rounded":
        return box.ROUNDED
    if border == "square":
        return box.SQUARE
    if border == "ascii":
        return box.ASCII
    if border == "none":
        return None
    raise ValueError(f"unknown border style: {border!r}")


def _resolve_path(row: Row, segments: list[str]) -> Any:
    value: Any = row
    for key in segments:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _render_cell(value: Any) -> str:
    if isinstance(value, list) and all(_is_scalar(v) for v in value):
        return ", ".join("" if v is None else str(v) for v in value)
    return "" if value is None else str(value)


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, str | int | float | bool)


def _role_style(theme: ThemeSpec, role: str, *, colorize: bool) -> str | None:
    if not colorize:
        return None
    return theme.color_roles.get(role)


def stream_is_tty(stream: TextIO) -> bool:
    isatty = getattr(stream, "isatty", None)
    if not callable(isatty):
        return False
    try:
        return bool(isatty())
    except OSError:
        return False


def should_colorize(stream: TextIO) -> bool:
    """Decide whether to emit ANSI color for ``stream``.

    Precedence (the de-facto cross-tool convention):

    1. ``NO_COLOR`` set to any non-empty value → never color (opt-out wins).
    2. ``FORCE_COLOR`` set to any non-empty value → always color.
    3. Otherwise auto-detect from ``stream.isatty()``.

    Color is on/off only; ``FORCE_COLOR``'s 1/2/3 depth levels are not honoured.
    """
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return stream_is_tty(stream)


def _render_text(text: Text, *, colorize: bool) -> str:
    return _render_rich(text, colorize=colorize)


def _styled_text(value: str, style: str | None) -> Text:
    if style is None:
        return Text(value)
    return Text(value, style=style)


def _render_rich(renderable: Table | Text, *, colorize: bool) -> str:
    buf = io.StringIO()
    width = shutil.get_terminal_size(fallback=(80, 24)).columns
    Console(
        file=buf,
        force_terminal=colorize,
        color_system="standard" if colorize else None,
        no_color=not colorize,
        width=width,
    ).print(renderable)
    return buf.getvalue().rstrip()
