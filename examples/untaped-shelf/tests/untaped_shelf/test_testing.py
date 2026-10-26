"""``untaped_shelf.testing``: the checks ``check`` runs on every provider of the contract."""

from __future__ import annotations

import pytest
from untaped_shelf.api import Book, BookSource
from untaped_shelf.testing import conformance

from untaped.sdk.contracts import Provider


class FakeBooks(BookSource, Provider):
    def books(self) -> list[Book]:
        return [Book(title="Dune", pages=412), Book(title="Emma", pages=474)]


class Twins(BookSource, Provider):
    def books(self) -> list[Book]:
        return [Book(title="Dune"), Book(title="Dune")]


def test_conformance_fails_a_provider_with_shared_titles() -> None:
    conformance(FakeBooks())
    with pytest.raises(AssertionError, match="books share a title: Dune"):
        conformance(Twins())
