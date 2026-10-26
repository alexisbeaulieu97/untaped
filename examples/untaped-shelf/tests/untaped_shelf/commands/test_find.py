"""``untaped shelf find``."""

from __future__ import annotations

from collections.abc import Callable

from untaped_shelf.api import Book, BookSource
from untaped_shelf.commands.find import Find
from untaped_shelf.plugin import Shelf

from untaped.sdk.contracts import Provider
from untaped.sdk.testing import Install

Installer = Callable[..., Install]


class FakeBooks(BookSource, Provider):
    """A provider the test controls: no settings, fixed books."""

    def books(self) -> list[Book]:
        return [Book(title="Dune", pages=412), Book(title="Emma", pages=474)]


def test_shelf_finds_the_one_book_with_a_title(install: Installer) -> None:
    ran = install(Shelf, providers={"fake": (FakeBooks,)}).run(Find(title="Emma"))
    assert ran.exit == 0, ran.stderr
    assert [book.pages for book in ran.rows_as(Book)] == [474]


def test_shelf_find_of_a_title_nobody_keeps_exits_2(install: Installer) -> None:
    ran = install(Shelf, providers={"fake": (FakeBooks,)}).cli("shelf find Ulysses")
    assert ran.exit == 2
    assert any("no provider of shelf.book_source matched" in line for line in ran.stderr)
