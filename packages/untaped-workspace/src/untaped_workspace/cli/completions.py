"""Typer autocompletion callbacks for workspace names.

Defensive: completion must never raise — any error returns an empty
list (matches the AWX completion's contract). Set
``UNTAPED_COMPLETION_DEBUG=1`` to surface a single stderr line naming
the cause; opt-in because shells discard completion stderr
inconsistently and the noise isn't worth it for healthy configs.
"""

from __future__ import annotations

import os
from collections.abc import Iterable

import typer

from untaped_workspace.infrastructure import WorkspaceRegistryRepository


def complete_workspace_name(incomplete: str) -> Iterable[str]:
    try:
        names = [w.name for w in WorkspaceRegistryRepository().entries()]
    except Exception as exc:
        if os.environ.get("UNTAPED_COMPLETION_DEBUG") == "1":
            typer.echo(
                f"warning: completion: {type(exc).__name__}: {exc}",
                err=True,
            )
        return []
    return [n for n in names if n.startswith(incomplete)]
