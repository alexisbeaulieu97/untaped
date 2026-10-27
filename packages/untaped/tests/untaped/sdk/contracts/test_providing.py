"""``providing``: provider classes composed as a plugin of their own (S3)."""

from __future__ import annotations

from abc import abstractmethod

import pytest
from pydantic import BaseModel

from untaped.runtime import bootstrap
from untaped.runtime.contracts.registry import reset
from untaped.runtime.registry import CompositionResult, PluginCandidate
from untaped.sdk import Plugin, experimental
from untaped.sdk.contracts import Contract, Issued, Ok, Provider, gather, providing
from untaped.sdk.errors import ConfigError


class Title(Issued):
    name: str


@experimental
class Titles(Contract):
    """Every title a plugin holds."""

    @abstractmethod
    def titles(self) -> list[Title]:
        """Every title."""


class _Plain(BaseModel):
    """No settings."""


class _Shelf(Plugin):
    """Own the titles."""

    settings = _Plain
    offers = (Titles,)


class _Shop(Titles, Provider):
    def titles(self) -> list[Title]:
        return [Title(name="Emma")]


class _Store(Plugin):
    """Sell titles."""

    settings = _Plain
    fills = (_Shop,)


class _House(Titles, Provider):
    def titles(self) -> list[Title]:
        return [Title(name="Dune")]


def _installed() -> list[PluginCandidate]:
    return [
        PluginCandidate(distribution="untaped-shelf", name="shelf", target=_Shelf),
        PluginCandidate(distribution="untaped-shop", name="shop", target=_Store),
    ]


def _composed() -> CompositionResult:
    reset()
    return bootstrap.compose_root(candidates=_installed())


def test_the_classes_answer_as_the_named_plugin_for_the_block_only() -> None:
    before = _composed()
    with providing({"house": (_House,)}, candidates=_installed()):
        answers = gather(Titles.titles)()
        house = next(answer for answer in answers if answer.plugin == "house")
        assert isinstance(house, Ok)
        [title] = house.value
        assert title.source is not None and title.source.plugin == "house"
        assert {answer.plugin for answer in answers} == {"house", "shop"}
    assert bootstrap.composition() is before
    assert {answer.plugin for answer in gather(Titles.titles)()} == {"shop"}


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
    before = _composed()
    with (
        pytest.raises(ConfigError, match=message),
        providing({key: (_House,)}, candidates=_installed()),
    ):
        pass
    assert bootstrap.composition() is before


class _Loose(Titles):
    def titles(self) -> list[Title]:
        return []


@pytest.mark.parametrize("given", [_Loose, Titles, Title])
def test_a_class_that_is_no_provider_is_refused_naming_it(given: type) -> None:
    _composed()
    with (
        pytest.raises(TypeError, match=rf"holds .*{given.__qualname__}.*, not a provider"),
        providing({"house": (given,)}, candidates=_installed()),
    ):
        pass


@pytest.mark.parametrize(
    ("key", "taken_by"),
    [("untaped", "core"), ("plugin", "a root command"), ("profiles", "a config key")],
)
def test_a_key_the_composition_quarantines_is_refused_and_the_composition_comes_back(
    key: str, taken_by: str
) -> None:
    before = _composed()
    with (
        pytest.raises(ConfigError, match=f"{key}: duplicate-name: .* is taken by {taken_by}"),
        providing({key: (_House,)}, candidates=_installed()),
    ):
        pass
    assert bootstrap.composition() is before
