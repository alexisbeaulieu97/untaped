"""The library's provider of ``shelf``'s BookSource contract.

It imports only ``untaped.contracts``, ``untaped.sdk`` and ``untaped_shelf.api``.
"""

from __future__ import annotations

from untaped_shelf.api import Book, BookSource

from untaped.contracts import Configured, NotReady
from untaped_library.records import Volume
from untaped_library.settings import LibrarySettings


class LibraryBooks(BookSource[Volume], Configured[LibrarySettings]):
    """Lends the configured volumes as books; each book's source keeps its volume."""

    def ready(self) -> NotReady | None:
        """Ready once ``library.volumes`` lists a volume (settings that validate aren't enough)."""
        if not self.settings.volumes:
            return NotReady("no volumes configured", setting="library.volumes")
        return None

    def to_book(self, item: Volume) -> Book:
        return Book(title=item.name, pages=item.pages)

    def books(self) -> list[Book]:
        return [
            self.to_book(Volume.model_validate(entry.model_dump()))
            for entry in self.settings.volumes
        ]
