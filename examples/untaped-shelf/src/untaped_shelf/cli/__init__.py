"""The ``untaped shelf`` commands."""

from __future__ import annotations

from typing import Annotated

from cyclopts import Parameter

from untaped.contracts import Failed, Ok, Skipped, gather, select_one
from untaped.sdk import (
    FormatOption,
    create_app,
    emit,
    finish,
    report_error,
    report_errors,
    ui_context,
)
from untaped_shelf.api import Book, BookSource

app = create_app(name="shelf", help="Find books (an example contract owner).")


@app.command(name="list")
def list_books(*, fmt: FormatOption = "table") -> None:
    """List every book every provider keeps; a provider that failed makes it exit non-zero."""
    with report_errors():
        answers = gather(BookSource.books)()
    books: list[Book] = []
    for answer in answers:
        if isinstance(answer, Ok):
            books.extend(answer.value)
            if answer.stale is not None:
                ui_context().message(
                    "warning", f"{answer.plugin}: cached answer ({answer.stale.error})"
                )
        elif isinstance(answer, Failed):
            report_error(answer.error, item=answer.plugin)
        elif isinstance(answer, Skipped) and answer.reason != "not-configured":
            ui_context().message("warning", f"{answer.plugin} wasn't asked: {answer.detail}")
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
