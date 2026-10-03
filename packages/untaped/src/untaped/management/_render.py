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

from collections.abc import Callable, Sequence

from rich.text import Text

from untaped.cli import emit_with
from untaped.theme import OutputFormat
from untaped.ui import UiContext, ui_context

_UNICODE_GLYPHS = {"pass": "✓", "warn": "⚠", "fail": "✗", "fix": "→"}
_ASCII_GLYPHS = {"pass": "+", "warn": "!", "fail": "x", "fix": "->"}
#: Per status: the theme color role that styles its glyph.
_STATUS_ROLES = {"pass": "success", "warn": "warning", "fail": "error"}


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
    ``(automatic)`` when the fix is. Written to stdout in one call, themed
    with the default theme when settings are broken.
    """
    ui = ui_context(strict=False)
    glyphs = _ASCII_GLYPHS if ui.theme.border == "ascii" else _UNICODE_GLYPHS
    groups: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        groups.setdefault(str(row["capability"]), []).append(row)
    check_width = max((len(str(row["check"])) for row in rows), default=0)
    title_width = max((len(str(row["title"])) for row in rows), default=0)
    indent = " " * (4 + check_width + 2)
    lines: list[Text] = []
    for capability, members in groups.items():
        lines.append(Text(capability, style=_style(ui, "header", "bold")))
        for row in members:
            status = str(row["status"])
            line = Text("  ")
            line.append(glyphs.get(status, "?"), style=_style(ui, _STATUS_ROLES.get(status)))
            line.append(f" {str(row['check']).ljust(check_width)}  ")
            line.append(f"{str(row['title']).ljust(title_width)}  {row['detail']}")
            lines.append(line)
            fix = row.get("fix")
            if status != "pass" and isinstance(fix, list) and fix:
                tag = "  (automatic)" if row.get("automatic") else ""
                lines.append(Text(f"{indent}{glyphs['fix']} {run_line(fix)}{tag}"))
    ui.styled(Text("\n").join(lines))


def _style(ui: UiContext, role: str | None, default: str = "") -> str:
    """The theme's style for ``role``, else ``default``."""
    return (ui.theme.color_roles.get(role) if role else None) or default


__all__ = ["emit_check_list", "emit_isolated"]
