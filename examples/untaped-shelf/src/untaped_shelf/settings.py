"""The ``shelf`` config section.

A ``@cached`` contract method's cache is the owner's, named
``<contract>_<method>``: ``BookSource.books`` is kept as ``book_source_books``,
so its max age is the user's setting
``shelf.caches.book_source_books.max_age_seconds``.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ShelfSettings(BaseModel):
    """shelf has nothing of its own to configure; core adds its providers' ranking."""

    model_config = ConfigDict(frozen=True)
