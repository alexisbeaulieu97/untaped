"""Root ``untaped plugin`` group and its ``list`` command (Wave 1.4, spec §7.3).

``plugin list`` reports one record per candidate
provider — ``name/status/distribution/version``, in name order — from the
composition outcome: ready rows for committed plugins plus quarantined
rows carrying the entry-point name (or the ``unknown`` sentinels when the
provider never resolved). The listing never touches settings, so invalid
plugin values cannot block it (spec §4 failure isolation).
"""

from __future__ import annotations

from collections.abc import Sequence

from cyclopts import App

from untaped.cli import (
    ColumnsOption,
    FormatOption,
    create_app,
    echo,
    report_errors,
)
from untaped.management._render import emit_isolated
from untaped.plugins.registry import (
    CompositionResult,
    ProviderCandidate,
    candidate_distribution,
)
from untaped.theme import OutputFormat

_UNKNOWN = "unknown"

#: Shown by root ``--help`` and an empty ``plugin list`` when a bare
#: ``untaped`` install has no plugin providers.
INSTALL_HINT = (
    "No plugins are installed. Reinstall untaped with an extra: 'untaped[all]' "
    "for all of them, or one, e.g. 'untaped[awx]'."
)


def build_root_plugin_app(
    *,
    result: CompositionResult,
    candidates: Sequence[ProviderCandidate],
) -> App:
    """Return the root ``plugin`` group for one composition."""
    app = create_app(name="plugin", help="Inspect installed plugins.")

    @app.command(name="list")
    def list_command(
        *,
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """List installed plugins and quarantined providers, one row each."""
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
    rows = _rows(result, candidates)
    emit_isolated(
        rows,
        fmt=fmt,
        columns=columns,
        kind="untaped.plugin",
        table_columns=["name", "status", "distribution", "version"],
    )
    if not rows:
        # ``sdk.hint`` only renders ``hint: run `<command>```, so print the prefix directly.
        echo(f"hint: {INSTALL_HINT}", err=True)


def _rows(
    result: CompositionResult,
    candidates: Sequence[ProviderCandidate],
) -> list[dict[str, object]]:
    # A distribution declares each entry-point name once, and a provider whose
    # spec name differs from it is quarantined, so the reported (distribution,
    # name) finds the candidate of every row, a provider that never resolved
    # and a blank distribution (reported as ``unknown``) included.
    versions = {
        (candidate_distribution(item), item.name): item.distribution_version or _UNKNOWN
        for item in candidates
    }
    ready = [
        (registered.spec.name, "ready", registered.provider_ref.distribution)
        for registered in result.plugins
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


__all__ = ["INSTALL_HINT", "build_root_plugin_app"]
