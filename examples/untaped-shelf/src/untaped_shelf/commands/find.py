"""``untaped shelf find``: the one book with a title."""

from __future__ import annotations

from pydantic import Field

from untaped.sdk import Command, Output, Positional
from untaped.sdk.contracts import gather, select_one
from untaped_shelf.api import Book, BookSource
from untaped_shelf.settings import ShelfSettings


class Find(Command[ShelfSettings]):
    """Find the one book titled TITLE."""

    title: Positional[str] = Field(description="The book's exact title.")
    output: Output[Book]

    def run(self) -> Book:
        answers = gather(BookSource.books)()
        return select_one(answers, lambda each: each.title == self.title)
