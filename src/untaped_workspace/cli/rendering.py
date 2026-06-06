"""Workspace CLI rendering helpers."""

from __future__ import annotations

from collections.abc import Sequence

from untaped import OutputFormat, UiContext, ui_context

Row = dict[str, object]


def render_rows(
    rows: Sequence[Row],
    *,
    fmt: OutputFormat,
    columns: list[str] | None,
) -> str:
    """Render row output using global UI settings only for human output."""
    ui = ui_context() if fmt == "table" else UiContext()
    return ui.collection(rows, fmt=fmt, columns=columns)
