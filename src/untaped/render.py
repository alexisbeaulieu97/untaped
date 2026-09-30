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
import re
import shutil
from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime
from enum import Enum
from typing import Any, Literal, Protocol, TextIO

import yaml
from rich import box
from rich.cells import cell_len
from rich.console import Console
from rich.table import Table
from rich.text import Text

from untaped.pipe import PIPE_ENVELOPE_VERSION, PIPE_MARKER_KEY
from untaped.theme import DEFAULT_SYMBOLS, BorderStyle, OutputFormat, ThemeSpec

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
            return json.dumps(selected, default=_json_default)
        if fmt == "yaml":
            return _dump_yaml(selected)
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
            return json.dumps(selected, default=_json_default)
        if fmt == "yaml":
            return _dump_yaml(selected)
        if fmt == "table":
            if theme.detail_view == "table":
                rows: list[Row] = [
                    {"field": key, "value": _table_cell(key, value)}
                    for key, value in selected.items()
                ]
                return _format_table(rows, theme, colorize=colorize, field_rows=True)
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


class _YamlDumper(yaml.SafeDumper):
    """Safe YAML dumper that renders unknown types instead of raising."""


def _represent_fallback(dumper: yaml.SafeDumper, value: object) -> yaml.Node:
    if isinstance(value, Enum):
        return dumper.represent_data(value.value)
    return dumper.represent_str(str(value))


def _represent_datetime(dumper: yaml.SafeDumper, value: datetime) -> yaml.Node:
    return dumper.represent_str(_utc(value))


def _utc(value: datetime) -> str:
    """A ``datetime`` in a plain row, as records render theirs (``…Z``, to the second)."""
    from untaped.records import format_utc  # noqa: PLC0415 - keep pydantic off render imports

    return format_utc(value)


def _json_default(value: object) -> str:
    return _utc(value) if isinstance(value, datetime) else str(value)


_YamlDumper.add_representer(None, _represent_fallback)  # type: ignore[arg-type]
_YamlDumper.add_representer(datetime, _represent_datetime)


def _dump_yaml(data: object) -> str:
    return yaml.dump(data, Dumper=_YamlDumper, sort_keys=False, default_flow_style=False).rstrip()


def _parse_columns(columns: list[str] | None) -> list[tuple[str, list[str]]] | None:
    return [(c, c.split(".")) for c in columns] if columns else None


def _select_rows(rows: Sequence[Row], parsed: list[tuple[str, list[str]]] | None) -> list[Row]:
    if parsed is None:
        return list(rows)
    return [_select_record(row, parsed) for row in rows]


def _select_record(record: Row, parsed: list[tuple[str, list[str]]] | None) -> Row:
    if parsed is None:
        return dict(record)
    return {name: _column_value(record, name, segments) for name, segments in parsed}


def _column_value(row: Mapping[str, Any], name: str, segments: list[str]) -> Any:
    return row[name] if name in row else _resolve_path(row, segments)


def column_value(row: Mapping[str, Any], name: str) -> Any:
    """The value ``--columns name`` selects from ``row``.

    A whole key (``has-file:release.txt``) wins over a dotted path
    (``summary_fields.project.name``); a missing path is ``None``.
    """
    return _column_value(row, name, name.split("."))


def _format_raw(rows: Sequence[Row], parsed: list[tuple[str, list[str]]] | None) -> str:
    if not rows:
        return ""
    if parsed is None:
        first_key = next(iter(rows[0]))
        return "\n".join(_render_cell(row.get(first_key, "")) for row in rows)
    return "\n".join(
        "\t".join(_render_cell(_column_value(row, name, segments)) for name, segments in parsed)
        for row in rows
    )


def _format_pipe(rows: Sequence[Row], kind: str | None) -> str:
    """Render rows as the self-describing NDJSON ``pipe`` envelope (full records)."""
    return "\n".join(
        json.dumps(
            {PIPE_MARKER_KEY: PIPE_ENVELOPE_VERSION, "kind": kind, "record": dict(row)},
            default=_json_default,
        )
        for row in rows
    )


def _format_table(
    rows: Sequence[Row], theme: ThemeSpec, *, colorize: bool, field_rows: bool = False
) -> str:
    """A themed table; ``field_rows`` marks a record's ``field``/``value`` rows."""
    if not rows:
        return ""
    compact = theme.density == "compact"
    box_style = _resolve_box(theme.border)
    table = Table(
        show_header=True,
        header_style=_role_style(theme, "header", colorize=colorize) or "",
        border_style=_role_style(theme, "border", colorize=colorize),
        box=box_style,
        padding=(0, 0) if compact else (0, 1),
    )
    columns = list(dict.fromkeys(key for row in rows for key in row))
    cells = [[_table_cell(col, row.get(col)) for col in columns] for row in rows]
    overhead = len(columns) * (0 if compact else 2) + (len(columns) + 1 if box_style else 0)
    widths = _fit_widths(columns, cells, budget=_output_size()[0] - overhead)
    for col, width in zip(columns, widths, strict=True):
        # A cell too wide for its column ends in an ellipsis, so a row stays one
        # line; an explanation (why a row failed) wraps instead, to be read whole.
        wrap = col in _WRAPPED_COLUMNS
        table.add_column(
            Text(col), width=width, no_wrap=not wrap, overflow="fold" if wrap else "ellipsis"
        )
    value_style = _role_style(theme, "value", colorize=colorize)
    for row, texts in zip(rows, cells, strict=True):
        table.add_row(
            *[
                _styled_text(
                    text,
                    _status_style(
                        theme, _named(row, col, field_rows), row.get(col), colorize=colorize
                    )
                    or value_style,
                )
                for col, text in zip(columns, texts, strict=True)
            ]
        )
    return _render_rich(table, colorize=colorize)


#: Columns explaining a row (why it failed or was skipped): wrapped, never cut.
_WRAPPED_COLUMNS = frozenset({"detail", "message", "hint"})


def _min_width(column: str, *, first: bool) -> int:
    """How narrow a column may get while another can still give way.

    The first column identifies the row and a wrapped column explains it, so
    both keep more room; any other keeps short values (``updated``) whole.
    """
    if first:
        return 40
    return 30 if column in _WRAPPED_COLUMNS else 10


def _named(row: Row, column: str, field_rows: bool) -> str:
    """The field a cell holds: its column, or in ``field_rows`` the row's ``field``."""
    return str(row["field"]) if field_rows and column == "value" else column


def _fit_widths(columns: list[str], cells: list[list[str]], *, budget: int) -> list[int | None]:
    """Column widths that fit ``budget`` by narrowing only the widest columns.

    Every column wider than a common cap is cut to it, the cap being the
    largest that fits. A column keeps at least :func:`_min_width` while that
    fits, else as much of it as fits for every column, and at least its
    header. ``None`` keeps a column's natural width (everything fits, or
    nothing can).
    """
    if not columns:
        return []
    natural = [
        max(cell_len(col), *(cell_len(texts[i]) for texts in cells))
        for i, col in enumerate(columns)
    ]
    if sum(natural) <= budget:
        return [None] * len(columns)
    headers = [cell_len(col) for col in columns]
    preferred = [
        max(header, _min_width(col, first=i == 0))
        for i, (header, col) in enumerate(zip(headers, columns, strict=True))
    ]
    for tenths in range(10, -1, -1):
        # Shrink every minimum toward its header until the minimums fit.
        floor = [
            min(width, header + (low - header) * tenths // 10)
            for width, header, low in zip(natural, headers, preferred, strict=True)
        ]
        if sum(floor) <= budget:
            cap = _widest_cap(natural, floor, budget=budget)
            return [max(low, min(width, cap)) for width, low in zip(natural, floor, strict=True)]
    return [None] * len(columns)


def _widest_cap(natural: list[int], floor: list[int], *, budget: int) -> int:
    """The largest cap on column widths (never below ``floor``) within ``budget``."""

    def total(cap: int) -> int:
        return sum(max(low, min(width, cap)) for width, low in zip(natural, floor, strict=True))

    low, high = 0, max(natural)
    while low < high:
        cap = (low + high + 1) // 2
        low, high = (cap, high) if total(cap) <= budget else (low, cap - 1)
    return low


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
    value_style = _status_style(theme, key, value, colorize=colorize) or _role_style(
        theme, "value", colorize=colorize
    )
    rendered_value = _flat(value)
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


def _resolve_path(row: Mapping[str, Any], segments: list[str]) -> Any:
    value: Any = row
    for key in segments:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def _render_cell(value: Any) -> str:
    if isinstance(value, datetime):
        return _utc(value)
    if isinstance(value, list) and all(_is_scalar(v) for v in value):
        return ", ".join("" if v is None else str(v) for v in value)
    return "" if value is None else str(value)


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, str | int | float | bool)


#: Columns whose value is a git object name, shortened in tables.
_SHA_COLUMNS = frozenset({"sha", "commit", "revision", "scm_revision"})
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SHORT_SHA = 10


def _table_cell(column: str, value: Any) -> str:
    """One table cell: flat, single-line text a human scans across a row.

    Only the ``table`` format uses it; json, yaml, raw and pipe keep values
    verbatim. Mappings flatten to ``key=value`` pairs, empty containers are
    blank, durations (``*_s``, ``elapsed``) read as ``1m42s``, other floats keep two
    decimals, a 40-hex commit is shortened, and whitespace runs (newlines
    included) collapse to one space.
    """
    if isinstance(value, int | float) and not isinstance(value, bool) and _is_duration(column):
        return _duration(value)
    if isinstance(value, float):
        return f"{value:.2f}".rstrip("0").rstrip(".")
    if isinstance(value, str) and column in _SHA_COLUMNS and _SHA_RE.match(value):
        return value[:_SHORT_SHA]
    return " ".join(_flat(value).split())


def _flat(value: Any) -> str:
    if isinstance(value, Mapping):
        return ", ".join(f"{key}={text}" for key, text in _flat_pairs(value, prefix=""))
    if isinstance(value, list | tuple):
        separator = "; " if any(isinstance(item, Mapping) for item in value) else ", "
        return separator.join(text for item in value if (text := _flat(item)))
    return _render_cell(value)


def _flat_pairs(mapping: Mapping[str, Any], *, prefix: str) -> Iterator[tuple[str, str]]:
    for key, value in mapping.items():
        if isinstance(value, Mapping):
            yield from _flat_pairs(value, prefix=f"{prefix}{key}.")
        elif text := _flat(value):
            yield f"{prefix}{key}", text


def _is_duration(column: str) -> bool:
    """Seconds by name: ``duration_s``, ``wait_s``, or AWX's ``elapsed``."""
    return column.endswith("_s") or column == "elapsed"


def _duration(seconds: float) -> str:
    if round(seconds, 1) < 60:
        return f"{seconds:.1f}s"
    minutes, secs = divmod(round(seconds), 60)
    if minutes < 60:
        return f"{minutes}m{secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


#: Columns holding an outcome or state word, colored by what the word means.
_STATUS_COLUMNS = frozenset({"status", "action", "result", "state", "job_status"})
_STATUS_ROLES: dict[str, str] = {
    **dict.fromkeys(
        ("failed", "failure", "fail", "error", "errored", "conflict", "partial"), "error"
    ),
    **dict.fromkeys(
        (
            "warn",
            "warning",
            "skipped",
            "pending",
            "waiting",
            "running",
            "planned",
            "stale",
            "outdated",
            "unavailable",
            "canceled",
            "cancelled",
            "cancel_requested",
        ),
        "warning",
    ),
    **dict.fromkeys(
        (
            "successful",
            "success",
            "pass",
            "passed",
            "ok",
            "ready",
            "created",
            "updated",
            "deleted",
            "synced",
            "cloned",
            "added",
            "removed",
            "completed",
        ),
        "success",
    ),
}
#: Used when the theme leaves a status role uncolored (the default theme).
_STATUS_FALLBACK = {"error": "red", "warning": "yellow", "success": "green"}


def _status_style(theme: ThemeSpec, column: str, value: Any, *, colorize: bool) -> str | None:
    if not colorize or column not in _STATUS_COLUMNS or not isinstance(value, str):
        return None
    role = _STATUS_ROLES.get(value.lower())
    if role is None:
        return None
    # A role the theme sets to "" turns the color off.
    style = theme.color_roles.get(role, _STATUS_FALLBACK[role])
    return style or None


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
    except OSError, ValueError:  # ValueError: a closed stream
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


def render_styled(text: Text | str, *, colorize: bool, truncate: bool = False) -> str:
    """Render one Rich ``Text`` line (a plain ``str`` is taken literally).

    ANSI styling is kept only when ``colorize``; the line wraps at the
    terminal width (never when output is not a terminal), or with
    ``truncate`` ends in an ellipsis there instead, and carries no trailing
    newline.
    """
    return _render_rich(
        text if isinstance(text, Text) else Text(text), colorize=colorize, truncate=truncate
    )


def _render_text(text: Text, *, colorize: bool) -> str:
    return _render_rich(text, colorize=colorize)


def _styled_text(value: str, style: str | None) -> Text:
    if style is None:
        return Text(value)
    return Text(value, style=style)


#: Rich needs a width; output that is not a terminal gets one no line reaches.
_UNBOUNDED_WIDTH = 1_000_000


def _output_size() -> tuple[int, int]:
    """``COLUMNS``, else the terminal's size, else (piped) no wrapping at all."""
    size = shutil.get_terminal_size(fallback=(0, 0))
    if size.columns <= 0:
        return _UNBOUNDED_WIDTH, 25
    return size.columns, size.lines if size.lines > 0 else 25


def _render_rich(renderable: Table | Text, *, colorize: bool, truncate: bool = False) -> str:
    buf = io.StringIO()
    # An explicit height too: with only a width Rich pins a TERM=dumb terminal to 80.
    width, height = _output_size()
    Console(
        file=buf,
        force_terminal=colorize,
        color_system="standard" if colorize else None,
        no_color=not colorize,
        width=width,
        height=height,
    ).print(
        renderable,
        # Console.print re-wraps a Text by its own flags, not the Text's.
        no_wrap=truncate or None,
        overflow="ellipsis" if truncate else None,
    )
    return buf.getvalue().rstrip()
