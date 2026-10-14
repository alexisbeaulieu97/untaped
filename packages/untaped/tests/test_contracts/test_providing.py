"""``providing``: provider instances composed as a plugin of their own (S3)."""

from __future__ import annotations

import pytest

from test_contracts.support import Book, BookSource, compose, shelf_spec, shop_spec
from untaped import bootstrap
from untaped.contracts import Ok, gather, providing
from untaped.errors import ConfigError
from untaped.plugins.registry import PluginCandidate
from untaped.testing import plugin_candidate


class _House(BookSource):
    def books(self) -> list[Book]:
        return [Book(title="Dune")]


def _installed() -> list[PluginCandidate]:
    return [plugin_candidate(shelf_spec()), plugin_candidate(shop_spec())]


def test_the_instances_answer_as_the_named_plugin_and_the_composition_comes_back() -> None:
    before = compose(shelf_spec(), shop_spec())
    with providing("shelf", {"house": [_House()]}, candidates=_installed()):
        answers = gather(BookSource.books)()
        house = next(answer for answer in answers if answer.plugin == "house")
        assert isinstance(house, Ok)
        [book] = house.value
        assert book.source is not None and book.source.plugin == "house"
        assert {answer.plugin for answer in answers} == {"house", "shop"}
    assert bootstrap.composition() is before


@pytest.mark.parametrize(
    ("key", "message"),
    [
        ("untaped", "reserved plugin name"),
        ("sdk", "reserved plugin name"),
        ("doctor", "reserved"),
        ("shop", "the name of an installed plugin"),
        ("Bad Key", "must be lowercase words"),
    ],
)
def test_a_key_that_could_shadow_a_plugin_is_refused_before_composing(
    key: str, message: str
) -> None:
    before = compose(shelf_spec(), shop_spec())
    with (
        pytest.raises(ConfigError, match=message),
        providing("shelf", {key: [_House()]}, candidates=_installed()),
    ):
        pass
    assert bootstrap.composition() is before


def test_an_instance_of_another_owners_contract_is_refused() -> None:
    compose(shelf_spec())
    with (
        pytest.raises(LookupError, match="not lint's"),
        providing("lint", {"house": [_House()]}, candidates=_installed()),
    ):
        pass
