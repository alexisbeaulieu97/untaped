"""Root ``untaped capabilities`` command (Wave 1.4, spec §7.3).

A terminal command (not a group) reporting one record per candidate
provider — ``name/status/distribution/version``, in name order — from the
composition outcome: ready rows for committed capabilities plus quarantined
rows carrying the entry-point name (or the ``unknown`` sentinels when the
provider never resolved). The listing never touches settings, so invalid
capability values cannot block it (spec §4 failure isolation).
"""

from __future__ import annotations

from collections.abc import Sequence

from cyclopts import App

from untaped.capabilities.registry import (
    CompositionResult,
    ProviderCandidate,
)
from untaped.cli import (
    ColumnsOption,
    FormatOption,
    create_app,
    report_errors,
)
from untaped.management._render import emit_isolated
from untaped.theme import OutputFormat

_UNKNOWN = "unknown"


def build_root_capabilities_app(
    *,
    result: CompositionResult,
    candidates: Sequence[ProviderCandidate],
) -> App:
    """Return the root ``capabilities`` terminal command for one composition."""
    app = create_app(
        name="capabilities",
        help="List composed capabilities and quarantined providers.",
    )

    @app.default
    def show_command(
        *,
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """List one record per candidate provider (spec §7.3)."""
        with report_errors():
            _show(result, candidates, fmt=fmt, columns=columns)

    return app


def _show(
    result: CompositionResult,
    candidates: Sequence[ProviderCandidate],
    *,
    fmt: OutputFormat,
    columns: list[str] | None,
) -> None:
    emit_isolated(
        _rows(result, candidates),
        fmt=fmt,
        columns=columns,
        kind="untaped.capability",
        table_columns=["name", "status", "distribution", "version"],
    )


def _rows(
    result: CompositionResult,
    candidates: Sequence[ProviderCandidate],
) -> list[dict[str, object]]:
    # A distribution declares each entry-point name once, and a provider whose
    # spec name differs from it is quarantined, so (distribution, name) finds
    # the candidate of every row, a provider that never resolved included.
    versions = {
        (item.distribution, item.name): item.distribution_version or _UNKNOWN for item in candidates
    }
    ready = [
        (registered.spec.name, "ready", registered.provider_ref.distribution)
        for registered in result.capabilities
    ]
    quarantined = [
        (record.name, "quarantined", record.distribution) for record in result.quarantine
    ]
    return [
        {
            "name": name,
            "status": status,
            "distribution": distribution,
            "version": versions.get((distribution, name), _UNKNOWN),
        }
        for name, status, distribution in sorted(
            ready + quarantined, key=lambda row: (row[0], row[2])
        )
    ]


__all__ = ["build_root_capabilities_app"]
