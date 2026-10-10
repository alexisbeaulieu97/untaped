"""The example ``shelf`` plugin: it owns the ``BookSource`` contract and asks it.

``shelf`` never knows who keeps books: every installed plugin that fills
:class:`~untaped_shelf.api.BookSource` answers (``untaped-library`` does).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from untaped.sdk import PluginSpec

if TYPE_CHECKING:
    from cyclopts import App

    from untaped.contracts import Contract

__all__ = ["SPEC", "build_app"]


def build_app() -> App:
    """Nullary factory returning the shelf app (imported on first use)."""
    from untaped_shelf.cli import app  # noqa: PLC0415

    return app


def _contracts() -> Sequence[type[Contract]]:
    from untaped_shelf.api import BookSource  # noqa: PLC0415

    return (BookSource,)


SPEC = PluginSpec(
    name="shelf",
    app_factory=build_app,
    help="Find books (an example contract owner).",
    contracts=_contracts,
)
