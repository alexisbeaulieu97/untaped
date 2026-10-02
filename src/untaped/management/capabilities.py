"""Root ``untaped capabilities`` command (Wave 1.4, spec §7.3).

A terminal command (not a group) reporting one record per candidate
provider — ``name/origin/status/distribution/version`` — from the
composition outcome: ready rows for committed capabilities plus quarantined
rows carrying the entry-point name (or the ``unknown`` sentinels when the
provider never resolved). The listing never touches settings, so invalid
capability values cannot block it (spec §4 failure isolation).
"""

from __future__ import annotations

from collections.abc import Sequence
from importlib import metadata

from cyclopts import App

from untaped.capabilities.registry import (
    CompositionResult,
    ExternalProvider,
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
    candidates: Sequence[ExternalProvider],
    shell_distribution: str,
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
            _show(result, candidates, shell_distribution, fmt=fmt, columns=columns)

    return app


def _show(
    result: CompositionResult,
    candidates: Sequence[ExternalProvider],
    shell_distribution: str,
    *,
    fmt: OutputFormat,
    columns: list[str] | None,
) -> None:
    emit_isolated(
        _rows(result, candidates, shell_distribution),
        fmt=fmt,
        columns=columns,
        kind="untaped.capability",
        table_columns=["name", "origin", "status", "version"],
    )


def _rows(
    result: CompositionResult,
    candidates: Sequence[ExternalProvider],
    shell_distribution: str,
) -> list[dict[str, object]]:
    ordered = sorted(candidates, key=lambda item: (item.distribution, item.name))
    by_key = {(item.distribution, _candidate_key(item)): item for item in ordered}
    product_version = _product_version(shell_distribution)
    rows: list[dict[str, object]] = []
    succeeded: set[tuple[str, str]] = set()
    for registered in result.capabilities:
        ref = registered.provider_ref
        candidate = by_key.get((ref.distribution, ref.entry_point))
        version = product_version if ref.kind == "built-in" else _candidate_version(candidate)
        rows.append(
            {
                "name": registered.spec.name,
                "origin": ref.kind,
                "status": "ready",
                "distribution": ref.distribution,
                "version": version,
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
                "origin": "external",
                "status": "quarantined",
                "distribution": candidate.distribution,
                "version": _candidate_version(candidate),
            }
        )
    for record in records[len(failed) :]:
        rows.append(_orphan_row(record))
    return rows


def _candidate_key(candidate: ExternalProvider) -> str:
    if isinstance(candidate.target, str):
        return candidate.target
    return candidate.name


def _product_version(shell_distribution: str) -> str:
    """Product version for built-ins; ``unknown`` when metadata is missing.

    The listing must never block (spec §4), so an unresolvable distribution
    degrades instead of raising like ``--version`` does.
    """
    try:
        return metadata.version(shell_distribution)
    except metadata.PackageNotFoundError:
        return _UNKNOWN


def _candidate_version(candidate: ExternalProvider | None) -> str:
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
        "origin": _UNKNOWN,
        "status": "quarantined",
        "distribution": record.distribution,
        "version": _UNKNOWN,
    }


__all__ = ["build_root_capabilities_app"]
