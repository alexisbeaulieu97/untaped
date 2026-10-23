"""The ``untaped shelf`` commands."""

from __future__ import annotations

from typing import Annotated

from cyclopts import Parameter

from untaped.sdk import (
    FormatOption,
    app_context,
    create_app,
    emit,
    finish,
    report_errors,
)
from untaped.sdk.contracts import Failed, Ok, Skipped, gather, select_one
from untaped_shelf.api import Book, BookSource

app = create_app(name="shelf", help="Find books (an example contract owner).")


@app.command(name="list")
def list_books(*, fmt: FormatOption = "table") -> None:
    """List every book every provider keeps; a provider that failed makes it exit non-zero."""
    with report_errors():
        answers = gather(BookSource.books)()
    diag = app_context().diag
    books: list[Book] = []
    for answer in answers:
        if isinstance(answer, Ok):
            books.extend(answer.value)  # a stale one says so on stderr as it is read here
        elif isinstance(answer, Failed):
            diag.failure(answer.error, item=answer.plugin)
        elif isinstance(answer, Skipped) and answer.reason != "not-configured":
            diag.warning(f"{answer.plugin} wasn't asked: {answer.detail}")
    emit(books, fmt=fmt)
    finish(any(isinstance(answer, Failed) for answer in answers))


@app.command(name="find")
def find(
    title: Annotated[str, Parameter(help="The book's exact title.")],
    /,
    *,
    fmt: FormatOption = "table",
) -> None:
    """Find the one book titled TITLE."""
    with report_errors():
        answers = gather(BookSource.books)()
        book: Book = select_one(answers, lambda each: each.title == title)
        emit(book, fmt=fmt)
