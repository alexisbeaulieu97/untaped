"""The example owner, tested the way any third-party plugin would be.

The owner and a provider together are tested in ``untaped-library``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from untaped_shelf.api import Book, BookSource
from untaped_shelf.testing import conformance

from untaped.contracts import Ok, gather
from untaped.testing import assert_contract_schemas, check_conventions, compose_with


class FakeBooks(BookSource):
    """A provider the owner controls: no settings, fixed books."""

    def books(self) -> list[Book]:
        return [Book(title="Dune", pages=412), Book(title="Emma", pages=474)]


class Twins(FakeBooks):
    def books(self) -> list[Book]:
        return [Book(title="Dune"), Book(title="Dune")]


def test_shelf_follows_the_conventions() -> None:
    check_conventions("shelf")


def test_the_contract_keeps_its_schemas() -> None:
    assert_contract_schemas(BookSource, snapshots=Path(__file__).parent / "snapshots")


def test_a_fake_provider_answers_the_owners_question() -> None:
    with compose_with("shelf", provides={"fake": [FakeBooks()]}):
        [answer] = gather(BookSource.books, refresh=True)()
    assert isinstance(answer, Ok)
    assert [book.title for book in answer.value] == ["Dune", "Emma"]
    assert answer.value[0].source is not None
    assert answer.value[0].source.plugin == "fake"


def test_conformance_fails_a_provider_with_shared_titles() -> None:
    conformance(FakeBooks())
    with pytest.raises(AssertionError, match="books share a title: Dune"):
        conformance(Twins())
