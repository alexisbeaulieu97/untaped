"""Typer autocompletion callbacks for workspace names.

Registry-read failures (broken YAML in ``~/.untaped/config.yml``, a
malformed registry entry, …) are swallowed so typer's completion
machinery returns an empty list rather than a traceback the shell
drops on the floor. Set ``UNTAPED_COMPLETION_DEBUG=1`` to surface a
single stderr line naming the cause — opt-in because shells discard
completion stderr inconsistently and the noise isn't worth it for
healthy configs.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable

from untaped_core import UntapedError

from untaped_workspace.infrastructure import WorkspaceRegistryRepository


def complete_workspace_name(incomplete: str) -> Iterable[str]:
    try:
        names = [w.name for w in WorkspaceRegistryRepository().entries()]
    except UntapedError as exc:
        if os.environ.get("UNTAPED_COMPLETION_DEBUG") == "1":
            print(
                f"untaped: completion: registry unreadable: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
        return []
    return [n for n in names if n.startswith(incomplete)]
