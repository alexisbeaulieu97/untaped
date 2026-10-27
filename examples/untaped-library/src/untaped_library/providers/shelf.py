"""The library's provider of ``shelf``'s BookSource contract.

It imports only ``untaped.sdk.contracts``, ``untaped.sdk`` and ``untaped_shelf.api``.
"""

from __future__ import annotations

from untaped_shelf.api import Book, BookSource

from untaped.sdk import key
from untaped.sdk.contracts import NotReady, Provider
from untaped_library.records import Volume
from untaped_library.settings import LibrarySettings


class LibraryBooks(BookSource[Volume], Provider[LibrarySettings]):
    """Lends the configured volumes as books; each book's source keeps its volume."""

    def ready(self) -> NotReady | None:
        """Ready once ``library.volumes`` lists a volume (settings that validate aren't enough)."""
        if not self.settings.volumes:
            return NotReady("no volumes configured", key(LibrarySettings).volumes)
        return None

    def to_book(self, item: Volume) -> Book:
        return Book(title=item.name, pages=item.pages)

    def books(self) -> list[Book]:
        return [
            self.to_book(Volume.model_validate(entry.model_dump()))
            for entry in self.settings.volumes
        ]
