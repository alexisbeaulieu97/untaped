"""untaped-workspace: manage local git workspaces.

``app`` is re-exported lazily (PEP 562): the plugin manifest defers the CLI
import via ``CliSpec.import_path``, and loading ``untaped_workspace.plugin``
from the entry point imports this package ``__init__`` first — an eager
``from untaped_workspace.cli import app`` here would pull the whole CLI tree
onto the startup import path anyway.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cyclopts import App

__all__ = ["app"]


def __getattr__(name: str) -> App:
    if name == "app":
        # Deferred on purpose; see the module docstring.
        from untaped_workspace.cli import app  # noqa: PLC0415

        return app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
