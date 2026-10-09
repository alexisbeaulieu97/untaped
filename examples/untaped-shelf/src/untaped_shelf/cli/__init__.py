"""The ``untaped shelf`` commands."""

from __future__ import annotations

from typing import Annotated

from cyclopts import Parameter

from untaped.contracts import Ok, gather, select_one
from untaped.sdk import FormatOption, create_app, emit, report_errors
from untaped_shelf.api import Book, BookSource

app = create_app(name="shelf", help="Find books (an example contract owner).")


@app.command(name="list")
def list_books(*, fmt: FormatOption = "table") -> None:
    """List every book every provider keeps."""
    with report_errors():
        answers = gather(BookSource.books)()
        emit(
            [book for answer in answers if isinstance(answer, Ok) for book in answer.value], fmt=fmt
        )


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
