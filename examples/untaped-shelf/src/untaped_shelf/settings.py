"""What ``shelf`` keeps: the books each ``BookSource`` provider lists.

A ``@cached`` contract method's cache is the owner's, declared here and
listed on ``PluginSpec.caches``; its max age is the user's setting
``shelf.caches.book_source_books.max_age_seconds``.
"""

from __future__ import annotations

from datetime import timedelta

from untaped.sdk import Cache

BOOKS = Cache("shelf", "book_source_books", label="books", max_age=timedelta(hours=1))
