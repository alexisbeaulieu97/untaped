"""``untaped shelf show``."""

from __future__ import annotations

import json
from collections.abc import Callable

from untaped_shelf.api import Book, BookSource
from untaped_shelf.plugin import Shelf

from untaped.sdk.contracts import Provider
from untaped.sdk.testing import Install

Installer = Callable[..., Install]


class FakeBooks(BookSource, Provider):
    """A provider the test controls: no settings, fixed books."""

    def books(self) -> list[Book]:
        return [Book(title="Dune", pages=412), Book(title="Emma", pages=474)]


def _line(kind: str, **record: object) -> str:
    return json.dumps({"untaped": "1", "kind": kind, "record": record}) + "\n"


def test_shelf_shows_books_by_title(install: Installer) -> None:
    ran = install(Shelf, providers={"fake": (FakeBooks,)}).cli("shelf show Emma Dune -f json")
    assert ran.exit == 0, ran.stderr
    assert [book.pages for book in ran.rows_as(Book)] == [474, 412]


def test_shelf_shows_piped_books_without_asking(install: Installer) -> None:
    piped = _line("shelf.book", title="Ulysses", pages=730)
    shelf = install(Shelf, providers={"fake": (FakeBooks,)})
    ran = shelf.cli("shelf show --stdin -f json", input=piped)
    assert ran.exit == 0, ran.stderr
    assert [book.title for book in ran.rows_as(Book)] == ["Ulysses"]


def test_shelf_show_of_a_kind_nobody_reads_exits_2(install: Installer) -> None:
    piped = _line("acme.widget", title="Ulysses")
    ran = install(Shelf, providers={"fake": (FakeBooks,)}).cli("shelf show --stdin", input=piped)
    assert ran.exit == 2, ran.stderr
