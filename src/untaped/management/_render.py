"""Settings-isolated row rendering for root read-only commands (Wave 1.4).

``untaped doctor`` and ``untaped capabilities`` must render even when the
registered settings are invalid — that is precisely what they report — but
table rendering resolves the active theme from settings. This helper renders
exactly like :func:`untaped.cli.emit` when settings are healthy and falls
back to the default theme when they are not, so a broken section can never
block the listing itself (spec §4 failure isolation).
"""

from __future__ import annotations

from collections.abc import Sequence

from untaped.cli import emit_with
from untaped.theme import OutputFormat
from untaped.ui import ui_context


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


__all__ = ["emit_isolated"]
