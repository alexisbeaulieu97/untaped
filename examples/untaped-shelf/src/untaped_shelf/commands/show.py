"""``untaped shelf show``: books by title, or the books piped in."""

from __future__ import annotations

from collections.abc import Iterator

from pydantic import Field

from untaped.sdk import Command, Output, Positional, Stdin
from untaped.sdk.contracts import Answers, gather, select_one
from untaped_shelf.api import Book, BookSource
from untaped_shelf.settings import ShelfSettings


class Show(Command[ShelfSettings]):
    """Show the books titled TITLES, or the rows piped in with ``--stdin``.

    A piped row is a ``shelf.book`` or any provider's own record (a
    ``library.volume``), read as a book through that provider.
    """

    titles: Positional[list[str]] = Field(default_factory=list, description="Exact titles.")
    stdin: Stdin[Book] = Stdin(via=BookSource.to_book)
    output: Output[Book]

    def run(self) -> Iterator[Book]:
        books: Answers[list[Book]] | None = None
        for value in self.stdin.values(self.titles):
            if isinstance(value, Book):
                yield value
                continue
            if books is None:
                books = gather(BookSource.books)()
            yield select_one(books, lambda each, title=value: each.title == title)
