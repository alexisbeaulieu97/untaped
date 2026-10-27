"""What another plugin may import from ``shelf``: the book record and the contract."""

from __future__ import annotations

from abc import abstractmethod
from datetime import timedelta

from untaped.sdk import experimental
from untaped.sdk.contracts import Contract, Issued, Record, cached

__all__ = ["Book", "BookSource"]


class Book(Issued):
    """A book, whoever keeps it; ``source`` names the plugin that issued it."""

    title: str
    pages: int = 0


@experimental
class BookSource[T: Record = Book](Contract):
    """Where books come from. ``T`` is the provider's own record type; it defaults to Book."""

    def to_book(self, item: T) -> Book:
        """Turn the provider's own record into a Book (only when ``T`` is not Book)."""
        raise NotImplementedError

    @cached(max_age=timedelta(hours=1), label="books")
    @abstractmethod
    def books(self) -> list[Book]:
        """Every book the provider keeps."""
