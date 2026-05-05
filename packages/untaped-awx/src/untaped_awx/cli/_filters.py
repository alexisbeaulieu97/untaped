"""Shared parser for the CLI's ``--filter KEY=VALUE`` flag."""

from __future__ import annotations

import typer


def parse_filters(raw: list[str] | None) -> dict[str, str]:
    """Split repeated ``--filter KEY=VALUE`` entries into a params dict.

    Splits on the first ``=`` so values containing ``=`` survive intact
    (e.g. ``--filter description__icontains=foo=bar``). Malformed entries
    are rejected up front rather than silently posted to AWX, where they
    would surface as an opaque 400.
    """
    if not raw:
        return {}
    out: dict[str, str] = {}
    for entry in raw:
        key, sep, value = entry.partition("=")
        if not sep or not key:
            raise typer.BadParameter(
                f"--filter expects KEY=VALUE (got {entry!r})", param_hint="--filter"
            )
        out[key] = value
    return out
