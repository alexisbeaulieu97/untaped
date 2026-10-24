"""``providing``: provider instances composed as a plugin of their own (S3)."""

from __future__ import annotations

import pytest

from core_test_support.contracts.support import Book, BookSource, compose, shelf_spec, shop_spec
from untaped.runtime import bootstrap
from untaped.runtime.registry import PluginCandidate
from untaped.sdk.contracts import Ok, gather, providing
from untaped.sdk.errors import ConfigError
from untaped.sdk.testing import plugin_candidate


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


@pytest.mark.parametrize(
    ("key", "taken_by"),
    [("untaped", "core"), ("plugin", "a root command"), ("profiles", "a config key")],
)
def test_a_key_the_composition_quarantines_is_refused_and_the_composition_comes_back(
    key: str, taken_by: str
) -> None:
    before = compose(shelf_spec(), shop_spec())
    with (
        pytest.raises(ConfigError, match=f"{key}: duplicate-name: .* is taken by {taken_by}"),
        providing("shelf", {key: [_House()]}, candidates=_installed()),
    ):
        pass
    assert bootstrap.composition() is before
