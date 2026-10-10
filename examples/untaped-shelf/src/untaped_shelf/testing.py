"""shelf's checks on any provider of its contract: ``untaped plugin check`` runs them.

A provider's own tests may call :func:`conformance` too, on a provider
composed with ``untaped.testing.compose_with``.
"""

from __future__ import annotations

from untaped_shelf.api import BookSource

__all__ = ["conformance"]


def conformance(provider: BookSource) -> None:
    """Fail unless every book ``provider`` keeps has a title of its own."""
    titles = [book.title for book in provider.books()]
    assert all(title.strip() for title in titles), "a book has a blank title"
    duplicates = sorted({title for title in titles if titles.count(title) > 1})
    assert not duplicates, f"books share a title: {', '.join(duplicates)}"
