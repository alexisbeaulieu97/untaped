"""untaped-workspace: manage local git workspaces.

``app`` is re-exported lazily (PEP 562) so importing this package does not pull
the whole CLI tree onto the startup import path. ``__main__:main`` (and tests)
import ``app`` only when they actually need it.
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
