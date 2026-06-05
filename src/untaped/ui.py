"""Semantic UI primitives and theme-aware terminal rendering."""

from __future__ import annotations

import io
import json
import shutil
import sys
from collections.abc import Mapping, Sequence
from typing import Any, Literal, Protocol, TextIO, cast

import yaml
from pydantic import BaseModel, Field
from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

from untaped.errors import ConfigError

OutputFormat = Literal["json", "yaml", "table", "raw"]
BorderStyle = Literal["rounded", "square", "ascii", "none"]
CollectionView = Literal["table", "list"]
DetailView = Literal["list", "table"]
Density = Literal["normal", "compact"]
MessageKind = Literal["success", "warning", "error", "info"]

Row = dict[str, object]

DEFAULT_SYMBOLS: dict[str, str] = {
    "success": "",
    "warning": "",
    "error": "",
    "info": "",
}


class ThemeSpec(BaseModel):
    """Terminal presentation tokens and default semantic view choices."""

    border: BorderStyle = "rounded"
    density: Density = "normal"
    collection_view: CollectionView = "table"
    detail_view: DetailView = "list"
    symbols: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_SYMBOLS))
    color_roles: dict[str, str] = Field(default_factory=dict)


class UiSettings(BaseModel):
    """Global UI preferences loaded from the top-level ``ui`` config section."""

    theme: str = "default"
    border: BorderStyle | None = None
    density: Density | None = None
    collection_view: CollectionView | None = None
    detail_view: DetailView | None = None
    symbols: dict[str, str] = Field(default_factory=dict)
    color_roles: dict[str, str] = Field(default_factory=dict)

    def apply_to(self, theme: ThemeSpec) -> ThemeSpec:
        """Apply user overrides to a registered or built-in theme."""
        data = theme.model_dump()
        for field in ("border", "density", "collection_view", "detail_view"):
            value = getattr(self, field)
            if value is not None:
                data[field] = value
        data["symbols"] = {**theme.symbols, **self.symbols}
        data["color_roles"] = {**theme.color_roles, **self.color_roles}
        return ThemeSpec.model_validate(data)


class _HasUiSettings(Protocol):
    ui: UiSettings


BUILTIN_THEMES: dict[str, ThemeSpec] = {
    "default": ThemeSpec(),
    "plain": ThemeSpec(border="ascii"),
    "compact": ThemeSpec(density="compact"),
}


class Renderer(Protocol):
    """Renderer boundary for semantic UI primitives."""

    def render_collection(
        self,
        rows: Sequence[Row],
        *,
        fmt: OutputFormat,
        columns: list[str] | None,
        theme: ThemeSpec,
    ) -> str: ...

    def render_detail(
        self,
        record: Row,
        *,
        fmt: OutputFormat,
        columns: list[str] | None,
        theme: ThemeSpec,
    ) -> str: ...

    def render_message(self, kind: MessageKind, text: str, *, theme: ThemeSpec) -> str: ...


class RichTerminalRenderer:
    """Default renderer for human terminal output and structured formats."""

    def render_collection(
        self,
        rows: Sequence[Row],
        *,
        fmt: OutputFormat,
        columns: list[str] | None,
        theme: ThemeSpec,
    ) -> str:
        parsed = _parse_columns(columns)
        if fmt == "raw":
            return _format_raw(rows, parsed)

        selected = _select_rows(rows, parsed)
        if fmt == "json":
            return json.dumps(selected, default=str)
        if fmt == "yaml":
            return yaml.safe_dump(selected, sort_keys=False, default_flow_style=False).rstrip()
        if fmt == "table":
            if theme.collection_view == "list":
                return _format_records_as_lines(selected)
            return _format_table(selected, theme)

        raise ValueError(f"unknown format: {fmt!r}")

    def render_detail(
        self,
        record: Row,
        *,
        fmt: OutputFormat,
        columns: list[str] | None,
        theme: ThemeSpec,
    ) -> str:
        parsed = _parse_columns(columns)
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
                return _format_table(rows, theme)
            return _format_record_as_lines(selected)

        raise ValueError(f"unknown format: {fmt!r}")

    def render_message(self, kind: MessageKind, text: str, *, theme: ThemeSpec) -> str:
        symbol = theme.symbols.get(kind, DEFAULT_SYMBOLS[kind])
        prefix = f"{symbol} " if symbol else ""
        label = f"{kind}: " if kind in {"warning", "error"} else ""
        return f"{prefix}{label}{text}"


class UiContext:
    """Theme-aware UI context for commands and plugin CLIs."""

    def __init__(
        self,
        *,
        theme: ThemeSpec | None = None,
        renderer: Renderer | None = None,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
    ) -> None:
        self.theme = theme or BUILTIN_THEMES["default"]
        self.renderer = renderer or RichTerminalRenderer()
        self.stdout = stdout or sys.stdout
        self.stderr = stderr or sys.stderr

    def collection(
        self,
        rows: Sequence[Row],
        *,
        fmt: OutputFormat,
        columns: list[str] | None = None,
    ) -> str:
        return self.renderer.render_collection(rows, fmt=fmt, columns=columns, theme=self.theme)

    def detail(
        self,
        record: Row,
        *,
        fmt: OutputFormat,
        columns: list[str] | None = None,
    ) -> str:
        return self.renderer.render_detail(record, fmt=fmt, columns=columns, theme=self.theme)

    def message(self, kind: MessageKind, text: str) -> None:
        rendered = self.renderer.render_message(kind, text, theme=self.theme)
        print(rendered, file=self.stderr)


def ui_context(
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    strict: bool = True,
) -> UiContext:
    """Build a UI context from active settings and registered theme presets."""
    from untaped.plugin_registry import current_registry  # noqa: PLC0415
    from untaped.settings import get_settings  # noqa: PLC0415

    try:
        settings = cast(_HasUiSettings, get_settings())
        theme = resolve_theme(settings.ui, themes=current_registry().themes)
    except ConfigError:
        if strict:
            raise
        theme = BUILTIN_THEMES["default"]
    return UiContext(theme=theme, stdout=stdout, stderr=stderr)


def resolve_theme(
    settings: UiSettings | None = None,
    *,
    themes: Mapping[str, ThemeSpec] | None = None,
) -> ThemeSpec:
    """Resolve the active theme plus user overrides."""
    ui_settings = settings or UiSettings()
    available = {**BUILTIN_THEMES, **dict(themes or {})}
    theme = available.get(ui_settings.theme)
    if theme is None:
        valid = ", ".join(sorted(available))
        raise ConfigError(f"unknown UI theme: {ui_settings.theme!r}. Valid themes: {valid}")
    return ui_settings.apply_to(theme)


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


def _format_table(rows: Sequence[Row], theme: ThemeSpec) -> str:
    if not rows:
        return ""
    table = Table(
        show_header=True,
        header_style=theme.color_roles.get("header", "bold"),
        border_style=theme.color_roles.get("border"),
        box=_resolve_box(theme.border),
        padding=(0, 0) if theme.density == "compact" else (0, 1),
    )
    columns = list(rows[0].keys())
    for col in columns:
        table.add_column(col)
    for row in rows:
        table.add_row(*[Text(_render_cell(row.get(c, ""))) for c in columns])
    buf = io.StringIO()
    width = shutil.get_terminal_size(fallback=(80, 24)).columns
    Console(file=buf, force_terminal=False, width=width).print(table)
    return buf.getvalue().rstrip()


def _format_records_as_lines(rows: Sequence[Row]) -> str:
    return "\n\n".join(_format_record_as_lines(row) for row in rows)


def _format_record_as_lines(record: Row) -> str:
    return "\n".join(f"{key}: {_render_cell(value)}" for key, value in record.items())


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
