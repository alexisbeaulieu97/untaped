"""Semantic UI primitives and theme-aware terminal rendering."""

from __future__ import annotations

import io
import json
import shutil
import sys
from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from typing import Any, Literal, Protocol, TextIO, cast

import yaml
from pydantic import BaseModel, Field
from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

from untaped.errors import ConfigError
from untaped.progress import ProgressHandle, progress_reporter
from untaped.prompts import (
    PromptBackend,
    PromptChoice,
    PromptToolkitPromptBackend,
    handle_prompt_exception,
    prompt_style_from_roles,
)

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
        colorize: bool,
    ) -> str: ...

    def render_detail(
        self,
        record: Row,
        *,
        fmt: OutputFormat,
        columns: list[str] | None,
        theme: ThemeSpec,
        colorize: bool,
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


class UiContext:
    """Theme-aware UI context for commands and plugin CLIs."""

    def __init__(
        self,
        *,
        theme: ThemeSpec | None = None,
        renderer: Renderer | None = None,
        prompt_backend: PromptBackend | None = None,
        stdin: TextIO | None = None,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
        verbose: bool = False,
    ) -> None:
        self.theme = theme or BUILTIN_THEMES["default"]
        self.renderer = renderer or RichTerminalRenderer()
        self.verbose = verbose
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        self.stderr = stderr or sys.stderr
        self.prompt_backend = prompt_backend or PromptToolkitPromptBackend(
            stdin=self.stdin,
            stderr=self.stderr,
            style=prompt_style_from_roles(self.theme.color_roles),
        )

    def collection(
        self,
        rows: Sequence[Row],
        *,
        fmt: OutputFormat,
        columns: list[str] | None = None,
        empty: str | bool | None = None,
    ) -> str:
        rendered = self.renderer.render_collection(
            rows,
            fmt=fmt,
            columns=columns,
            theme=self.theme,
            colorize=_stream_is_tty(self.stdout),
        )
        if not rows and fmt == "table" and empty:
            note = empty if isinstance(empty, str) else "No results."
            print(
                self.renderer.render_message(
                    "info", note, theme=self.theme, colorize=_stream_is_tty(self.stderr)
                ),
                file=self.stderr,
            )
        return rendered

    def detail(
        self,
        record: Row,
        *,
        fmt: OutputFormat,
        columns: list[str] | None = None,
    ) -> str:
        return self.renderer.render_detail(
            record,
            fmt=fmt,
            columns=columns,
            theme=self.theme,
            colorize=_stream_is_tty(self.stdout),
        )

    def message(self, kind: MessageKind, text: str) -> None:
        rendered = self.renderer.render_message(
            kind,
            text,
            theme=self.theme,
            colorize=_stream_is_tty(self.stderr),
        )
        print(rendered, file=self.stderr)

    def progress(self, label: str) -> AbstractContextManager[ProgressHandle]:
        """Report progress for a blocking operation on stderr.

        TTY renders an animated spinner; non-TTY emits throttled lines; under
        ``verbose`` the wrapped tool's own output streams through. stdout stays
        untouched so piped data is never polluted.
        """
        return progress_reporter(
            label,
            stream=self.stderr,
            verbose=self.verbose,
            isatty=_stream_is_tty(self.stderr),
        )

    def confirm(self, message: str, *, default: bool = False) -> bool:
        """Prompt for a yes/no response."""
        self._ensure_promptable()
        try:
            return self.prompt_backend.confirm(message, default=default)
        except (ConfigError, EOFError, KeyboardInterrupt) as exc:
            raise handle_prompt_exception(exc) from exc

    def text(
        self,
        message: str,
        *,
        default: str | None = None,
        required: bool = True,
    ) -> str:
        """Prompt for visible text."""
        self._ensure_promptable()
        try:
            value = self.prompt_backend.text(message, default=default)
        except (ConfigError, EOFError, KeyboardInterrupt) as exc:
            raise handle_prompt_exception(exc) from exc
        return self._validate_prompt_text(value, required=required)

    def secret(
        self,
        message: str,
        *,
        confirmation: bool = False,
        required: bool = True,
    ) -> str:
        """Prompt for hidden text."""
        self._ensure_promptable()
        try:
            value = self.prompt_backend.secret(message, confirmation=confirmation)
        except (ConfigError, EOFError, KeyboardInterrupt) as exc:
            raise handle_prompt_exception(exc) from exc
        return self._validate_prompt_text(value, required=required)

    def select[T](
        self,
        message: str,
        choices: Sequence[PromptChoice[T]],
        *,
        default: T | None = None,
        search: bool = False,
    ) -> T:
        """Prompt for one typed choice."""
        self._ensure_promptable()
        self._validate_choices(choices)
        try:
            return self.prompt_backend.select(message, choices, default=default, search=search)
        except (ConfigError, EOFError, KeyboardInterrupt) as exc:
            raise handle_prompt_exception(exc) from exc

    def multiselect[T](
        self,
        message: str,
        choices: Sequence[PromptChoice[T]],
        *,
        defaults: Sequence[T] | None = None,
        min_count: int = 0,
    ) -> list[T]:
        """Prompt for multiple typed choices."""
        self._ensure_promptable()
        self._validate_choices(choices)
        selected_defaults = list(defaults or ())
        try:
            values = self.prompt_backend.multiselect(
                message,
                choices,
                defaults=selected_defaults,
            )
        except (ConfigError, EOFError, KeyboardInterrupt) as exc:
            raise handle_prompt_exception(exc) from exc
        if len(values) < min_count:
            raise ConfigError(f"select at least {min_count} value(s)")
        return values

    def _ensure_promptable(self) -> None:
        if not _stream_is_tty(self.stdin):
            raise ConfigError("interactive prompt requires a TTY on stdin")

    @staticmethod
    def _validate_prompt_text(value: str, *, required: bool) -> str:
        if required and not value.strip():
            raise ConfigError("no value received from prompt")
        return value

    @staticmethod
    def _validate_choices[T](choices: Sequence[PromptChoice[T]]) -> None:
        if not choices:
            raise ConfigError("prompt requires at least one choice")
        labels = [choice.label for choice in choices]
        if len(set(labels)) != len(labels):
            raise ConfigError("prompt choices must have unique labels")


def ui_context(
    *,
    stdin: TextIO | None = None,
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
    return UiContext(theme=theme, stdin=stdin, stdout=stdout, stderr=stderr)


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


def _stream_is_tty(stream: TextIO) -> bool:
    isatty = getattr(stream, "isatty", None)
    if not callable(isatty):
        return False
    try:
        return bool(isatty())
    except OSError:
        return False


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
