"""The ``shelf`` config section, and the cache its contract's answers are kept in.

A ``@cached`` contract method's cache is the owner's: :class:`Shelf` holds it
as ``book_source_books`` (``<contract>_<method>``), so its max age is the
user's setting ``shelf.caches.book_source_books.max_age_seconds``.
"""

from __future__ import annotations

from datetime import timedelta

from pydantic import BaseModel, ConfigDict

from untaped.sdk import Cache

#: The books each ``BookSource`` provider lists.
BOOKS = Cache(label="books", max_age=timedelta(hours=1))


class ShelfSettings(BaseModel):
    """shelf has nothing of its own to configure; core adds its providers' ranking."""

    model_config = ConfigDict(frozen=True)
