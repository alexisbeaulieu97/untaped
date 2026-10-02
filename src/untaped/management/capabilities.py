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
    QuarantineRecord,
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
    ordered = sorted(candidates, key=lambda item: (item.distribution, item.name))
    by_key = {(item.distribution, _candidate_key(item)): item for item in ordered}
    rows: list[dict[str, object]] = []
    succeeded: set[tuple[str, str]] = set()
    for registered in result.capabilities:
        ref = registered.provider_ref
        candidate = by_key.get((ref.distribution, ref.entry_point))
        rows.append(
            {
                "name": registered.spec.name,
                "status": "ready",
                "distribution": ref.distribution,
                "version": _candidate_version(candidate),
            }
        )
        succeeded.add((ref.distribution, ref.entry_point))
    failed = [
        item for item in ordered if (item.distribution, _candidate_key(item)) not in succeeded
    ]
    records = list(result.quarantine)
    for candidate in failed:
        rows.append(
            {
                "name": candidate.name,
                "status": "quarantined",
                "distribution": candidate.distribution,
                "version": _candidate_version(candidate),
            }
        )
    for record in records[len(failed) :]:
        rows.append(_orphan_row(record))
    return sorted(rows, key=lambda row: (str(row["name"]), str(row["distribution"])))


def _candidate_key(candidate: ProviderCandidate) -> str:
    if isinstance(candidate.target, str):
        return candidate.target
    return candidate.name


def _candidate_version(candidate: ProviderCandidate | None) -> str:
    if candidate is None or not candidate.distribution_version:
        return _UNKNOWN
    return candidate.distribution_version


def _orphan_row(record: QuarantineRecord) -> dict[str, object]:
    """Row for a quarantine record with no matching candidate (defensive).

    ``compose`` emits exactly one record per failed candidate, so this path
    is unreachable today; it keeps the listing total instead of crashing if
    the kernel ever changes shape.
    """
    return {
        "name": record.entry_point or record.distribution,
        "status": "quarantined",
        "distribution": record.distribution,
        "version": _UNKNOWN,
    }


__all__ = ["build_root_capabilities_app"]
