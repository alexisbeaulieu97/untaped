"""The ``library`` plugin class: no commands, one setting, and books for ``shelf``.

``fills`` lists the provider; core reads the owner from the contract it
subclasses and instantiates it at the first ask, so a run that asks no
contract never builds it.
"""

from __future__ import annotations

from untaped.sdk import Plugin
from untaped_library.providers.shelf import LibraryBooks
from untaped_library.settings import LibrarySettings


class Library(Plugin):
    """Lend books to shelf (an example contract provider)."""

    settings = LibrarySettings
    fills = (LibraryBooks,)


__all__ = ["Library"]
