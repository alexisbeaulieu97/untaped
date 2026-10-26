"""The ``shelf`` plugin class: it owns the ``BookSource`` contract and asks it.

The ``untaped.plugins`` entry point names :class:`Shelf`, in this leaf
module, so the rest of the package (``api``, which providers import)
imports without the commands.
"""

from __future__ import annotations

from untaped.sdk import Plugin
from untaped_shelf.api import BookSource
from untaped_shelf.commands.find import Find
from untaped_shelf.commands.list import List
from untaped_shelf.settings import BOOKS, ShelfSettings


class Shelf(Plugin):
    """Find books (an example contract owner)."""

    settings = ShelfSettings
    commands = (List, Find)
    offers = (BookSource,)
    book_source_books = BOOKS


__all__ = ["Shelf"]
