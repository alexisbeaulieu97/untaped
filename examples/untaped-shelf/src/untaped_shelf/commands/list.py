"""``untaped shelf list``: every book every provider keeps."""

from __future__ import annotations

from collections.abc import Iterator

from untaped.sdk import Command, Output
from untaped.sdk.contracts import Failed, Ok, Skipped, gather
from untaped_shelf.api import Book, BookSource
from untaped_shelf.settings import ShelfSettings


class List(Command[ShelfSettings]):
    """List every book every provider keeps; a provider that failed makes it exit non-zero."""

    output: Output[Book]

    def run(self) -> Iterator[Book]:
        diag = self.ctx.diag
        for answer in gather(BookSource.books)():
            if isinstance(answer, Ok):
                yield from answer.value  # a stale one says so on stderr as it is read here
            elif isinstance(answer, Failed):
                diag.failure(answer.error, item=answer.plugin)
            elif isinstance(answer, Skipped) and answer.reason != "not-configured":
                diag.warning(f"{answer.plugin} wasn't asked: {answer.detail}")
