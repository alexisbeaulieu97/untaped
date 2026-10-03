"""Settings-isolated row rendering for root read-only commands (Wave 1.4).

``untaped doctor`` and ``untaped capabilities`` must render even when the
registered settings are invalid — that is precisely what they report — but
table rendering resolves the active theme from settings. This helper renders
exactly like :func:`untaped.cli.emit` when settings are healthy and falls
back to the default theme when they are not, so a broken section can never
block the listing itself (spec §4 failure isolation).

:func:`emit_check_list` is the human view of doctor rows (``doctor`` and
``setup``): a checklist grouped by capability, one status glyph per row and
each fix under its row, with the same isolation.
"""

from __future__ import annotations

import textwrap
from collections.abc import Callable, Sequence

from rich.text import Text

from untaped.cli import emit_with
from untaped.render import output_width, status_role_style
from untaped.theme import OutputFormat
from untaped.ui import UiContext, ui_context

_UNICODE_GLYPHS = {"pass": "✓", "warn": "⚠", "fail": "✗", "fix": "→"}
_ASCII_GLYPHS = {"pass": "+", "warn": "!", "fail": "x", "fix": "->"}
#: Per status: the theme color role that styles its glyph.
_GLYPH_ROLES = {"pass": "success", "warn": "warning", "fail": "error"}


def emit_isolated(
    rows: Sequence[dict[str, object]],
    *,
    fmt: OutputFormat,
    columns: list[str] | None,
    kind: str | None = None,
    table_columns: list[str] | None = None,
) -> None:
    """Render row data without letting invalid settings block the output.

    Settings are broken is often what the caller reports, so a ``table``
    falls back to the default theme instead of failing the listing.
    """
    emit_with(
        rows,
        ui=ui_context(strict=False),
        fmt=fmt,
        columns=columns,
        kind=kind,
        table_columns=table_columns,
    )


def emit_check_list(
    rows: Sequence[dict[str, object]], *, run_line: Callable[[list[str]], str]
) -> None:
    """Print doctor rows as a checklist grouped by capability, in row order.

    A warned or failed row with a fix adds an ``→ untaped …`` line
    (``run_line`` turns the argv into the command line), tagged
    ``(automatic)`` when the fix is. A long detail wraps within its column
    (on its own lines under the title when the terminal is too narrow).
    Written to stdout in one call, themed with the default theme when
    settings are broken.
    """
    ui = ui_context(strict=False)
    glyphs = _ASCII_GLYPHS if ui.theme.border == "ascii" else _UNICODE_GLYPHS
    groups: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        groups.setdefault(str(row["capability"]), []).append(row)
    check_width = max((len(str(row["check"])) for row in rows), default=0)
    title_width = max((len(str(row["title"])) for row in rows), default=0)
    title_column = 4 + check_width + 2
    detail_column = title_column + title_width + 2
    width = output_width()
    lines: list[Text] = []
    for capability, members in groups.items():
        lines.append(Text(capability, style=ui.theme.color_roles.get("header") or "bold"))
        for row in members:
            status = str(row["status"])
            line = Text("  ")
            line.append(glyphs.get(status, "?"), style=_glyph_style(ui, status))
            line.append(f" {str(row['check']).ljust(check_width)}  ")
            line.append(str(row["title"]).ljust(title_width))
            detail = str(row["detail"])
            if width is None or width - detail_column >= _MIN_DETAIL_WIDTH:
                first, *rest = _wrap(detail, width, detail_column)
                line.append(f"  {first[detail_column:]}")
                lines += [line, *(Text(more) for more in rest)]
            else:
                line.rstrip()
                lines += [line, *(Text(more) for more in _wrap(detail, width, title_column))]
            fix = row.get("fix")
            if status != "pass" and isinstance(fix, list) and fix:
                tag = "  (automatic)" if row.get("automatic") else ""
                command = f"{glyphs['fix']} {run_line(fix)}{tag}"
                lines += [Text(more) for more in _wrap(command, width, title_column)]
    # Every detail and fix line fits already; only a row's own check and title
    # can overflow a very narrow terminal, and they end in an ellipsis there.
    ui.styled(Text("\n").join(lines), truncate=True)


#: Below this many columns for the detail, it goes on its own lines under the title.
_MIN_DETAIL_WIDTH = 24


def _wrap(text: str, width: int | None, column: int) -> list[str]:
    """``text`` indented to ``column`` and wrapped to fit before ``width`` (``None``: never)."""
    indent = " " * column
    if width is None:
        return [indent + text]
    wrapped = textwrap.wrap(text, max(width - column, 1), break_on_hyphens=False) or [""]
    return [indent + line for line in wrapped]


def _glyph_style(ui: UiContext, status: str) -> str:
    """A status glyph's style: the theme's role colour, green/yellow/red by default."""
    role = _GLYPH_ROLES.get(status)
    return (status_role_style(ui.theme, role) if role else None) or ""


__all__ = ["emit_check_list", "emit_isolated"]
