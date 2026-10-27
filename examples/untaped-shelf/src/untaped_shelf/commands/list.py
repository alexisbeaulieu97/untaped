"""``untaped shelf list``: every book every provider keeps."""

from __future__ import annotations

from collections.abc import Iterator

from untaped.sdk import Command, Output
from untaped.sdk.contracts import Reason, gather
from untaped_shelf.api import Book, BookSource
from untaped_shelf.settings import ShelfSettings


class List(Command[ShelfSettings]):
    """List every book every provider keeps; a provider that failed makes it exit non-zero."""

    output: Output[Book]

    def run(self) -> Iterator[Book]:
        diag = self.ctx.diag
        answers = gather(BookSource.books)()
        for errored in answers.errored:
            diag.failure(errored.error, item=errored.plugin)
        for skipped in answers.skipped:
            if skipped.reason != Reason.not_configured:
                diag.warning(f"{skipped.plugin} wasn't asked: {skipped.detail}")
        for ok in answers.ok:
            yield from ok.value  # a stale one says so on stderr as it is read here
