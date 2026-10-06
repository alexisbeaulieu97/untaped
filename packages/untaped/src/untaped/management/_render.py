"""Settings-isolated row rendering for root read-only commands (Wave 1.4).

``untaped doctor`` and ``untaped capabilities`` must render even when the
registered settings are invalid — that is precisely what they report — but
table rendering resolves the active theme from settings. This helper renders
exactly like :func:`untaped.cli.emit` when settings are healthy and falls
back to the default theme when they are not, so a broken section can never
block the listing itself (spec §4 failure isolation).

:func:`emit_check_list` is the human view of doctor rows (``doctor`` and
``setup``): a checklist grouped by capability, one status glyph per row and
each fix under its row, with the same isolation. :func:`emit_fix_plan` and
:func:`emit_fix_list` are ``doctor fix``'s plan and result in the same style.
"""

from __future__ import annotations

import textwrap
from collections.abc import Callable, Sequence
from typing import Any

from rich.text import Text

from untaped.cli import emit_with
from untaped.render import output_width, status_role_style
from untaped.theme import OutputFormat
from untaped.ui import UiContext, ui_context

_UNICODE_GLYPHS = {
    "pass": "✓",
    "fixed": "✓",
    "warn": "▲",
    "partial": "◐",
    "fail": "✗",
    "failed": "✗",
    "skipped": "○",
    "planned": "○",
    "fix": "→",
}
_ASCII_GLYPHS = {
    "pass": "+",
    "fixed": "+",
    "warn": "!",
    "partial": "~",
    "fail": "x",
    "failed": "x",
    "skipped": "-",
    "planned": "-",
    "fix": "->",
}
#: Per status: the theme color role that styles its glyph.
_GLYPH_ROLES = {
    "pass": "success",
    "fixed": "success",
    "warn": "warning",
    "partial": "warning",
    "fail": "error",
    "failed": "error",
}


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
    glyphs = _glyphs(ui)
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
            line = _glyph_line(ui, glyphs, status)
            line.append(f"{str(row['check']).ljust(check_width)}  ")
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


def emit_fix_plan(
    runnable: Sequence[tuple[str, list[str]]], manual: Sequence[tuple[str, list[str]]]
) -> None:
    """Print ``doctor fix``'s plan to stderr: each ``(command line, checks)`` to run, then the rest.

    Not muted by ``--quiet``: it precedes a question.
    """
    ui = ui_context(strict=False)
    arrow = _glyphs(ui)["fix"]
    width = max((len(command) for command, _checks in (*runnable, *manual)), default=0)
    lines: list[str] = []
    for heading, fixes in (("Fixes to run", runnable), ("Needs you", manual)):
        if fixes:
            lines.append(f"{heading} ({len(fixes)}):")
            lines += [f"  {arrow} {cmd.ljust(width)}  {', '.join(checks)}" for cmd, checks in fixes]
    ui.styled("\n".join(lines), err=True)


def emit_fix_list(rows: Sequence[dict[str, Any]], *, run_line: Callable[[list[str]], str]) -> None:
    """Print ``untaped.fix_outcome`` rows: one line per fix, glyph, command, action, detail.

    A long detail wraps within its column, as in :func:`emit_check_list`.
    """
    ui = ui_context(strict=False)
    glyphs = _glyphs(ui)
    commands = [run_line(list(row["fix"])) for row in rows]
    command_width = max((len(command) for command in commands), default=0)
    action_width = max((len(str(row["action"])) for row in rows), default=0)
    detail_column = 4 + command_width + 2 + action_width + 2
    width = output_width()
    lines: list[Text] = []
    for row, command in zip(rows, commands, strict=True):
        line = _glyph_line(ui, glyphs, str(row["action"]))
        line.append(f"{command.ljust(command_width)}  {str(row['action']).ljust(action_width)}")
        detail = str(row["detail"])
        if width is None or width - detail_column >= _MIN_DETAIL_WIDTH:
            first, *rest = _wrap(detail, width, detail_column)
            line.append(f"  {first[detail_column:]}")
            lines += [line, *(Text(more) for more in rest)]
        else:
            lines += [line, *(Text(more) for more in _wrap(detail, width, 4))]
    ui.styled(Text("\n").join(lines), truncate=True)


def _glyphs(ui: UiContext) -> dict[str, str]:
    """ASCII glyphs under an ASCII border (the ``ansible graph tree`` rule)."""
    return _ASCII_GLYPHS if ui.theme.border == "ascii" else _UNICODE_GLYPHS


def _glyph_line(ui: UiContext, glyphs: dict[str, str], status: str) -> Text:
    line = Text("  ")
    line.append(glyphs.get(status, "?"), style=_glyph_style(ui, status))
    line.append(" ")
    return line


__all__ = ["emit_check_list", "emit_fix_list", "emit_fix_plan", "emit_isolated"]
