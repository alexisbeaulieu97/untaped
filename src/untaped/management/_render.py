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

from untaped.cli import echo, emit
from untaped.errors import ConfigError
from untaped.render import OutputFormat
from untaped.ui import ui_context


def emit_isolated(
    rows: Sequence[dict[str, object]],
    *,
    fmt: OutputFormat,
    columns: list[str] | None,
) -> None:
    """Render row data without letting invalid settings block the output."""
    try:
        emit(rows, fmt=fmt, columns=columns)
    except ConfigError:
        # Settings are broken (this is often what the caller reports): the
        # themed render above raised before writing anything, so retry with
        # the default theme instead of failing the listing.
        ui = ui_context(strict=False)
        rendered = ui.collection(rows, fmt=fmt, columns=columns)
        if rendered:
            echo(rendered)


__all__ = ["emit_isolated"]
