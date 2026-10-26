"""``untaped shelf list``: asked of providers the test composes."""

from __future__ import annotations

from collections.abc import Callable

from untaped_shelf.api import Book, BookSource
from untaped_shelf.commands.list import List
from untaped_shelf.plugin import Shelf

from untaped.sdk.contracts import Provider
from untaped.sdk.testing import Install

Installer = Callable[..., Install]


class FakeBooks(BookSource, Provider):
    """A provider the test controls: no settings, fixed books."""

    def books(self) -> list[Book]:
        return [Book(title="Dune", pages=412), Book(title="Emma", pages=474)]


class Broken(BookSource, Provider):
    def books(self) -> list[Book]:
        raise RuntimeError("the catalogue is down")


def test_shelf_lists_every_book_a_provider_keeps_with_its_source(install: Installer) -> None:
    ran = install(Shelf, providers={"fake": (FakeBooks,)}).run(List())
    assert ran.exit == 0, ran.stderr
    books = ran.rows_as(Book)
    assert [book.title for book in books] == ["Dune", "Emma"]
    assert books[0].source is not None and books[0].source.plugin == "fake"


def test_shelf_list_exits_non_zero_when_a_provider_failed(install: Installer) -> None:
    shelf = install(Shelf, providers={"fake": (FakeBooks,), "broken": (Broken,)})
    ran = shelf.run(List())
    assert ran.exit != 0
    assert [book.title for book in ran.rows_as(Book)] == ["Dune", "Emma"]
    assert any("broken" in line and "the catalogue is down" in line for line in ran.stderr)


def test_shelf_list_with_no_provider_says_none_is_ready(install: Installer) -> None:
    ran = install(Shelf).cli("shelf list")
    assert ran.exit == 4
