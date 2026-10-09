"""What another plugin may import from ``shelf``: the book record and the contract."""

from __future__ import annotations

from abc import abstractmethod
from datetime import timedelta

from untaped.contracts import Contract, Issued, Record, bridge, cached
from untaped.sdk import experimental

__all__ = ["Book", "BookSource"]


class Book(Issued, kind="shelf.book"):
    """A book, whoever keeps it; ``source`` names the plugin that issued it."""

    title: str
    pages: int = 0


@experimental
class BookSource[T: Record = Book](Contract):
    """Where books come from. ``T`` is the provider's own record type; it defaults to Book."""

    @bridge
    def to_book(self, item: T) -> Book:
        """Turn the provider's own record into a Book (only when ``T`` is not Book)."""
        raise NotImplementedError

    @cached(ttl=timedelta(hours=1))
    @abstractmethod
    def books(self) -> list[Book]:
        """Every book the provider keeps."""
