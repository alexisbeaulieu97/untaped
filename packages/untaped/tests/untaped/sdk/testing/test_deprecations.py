"""``deprecation_of``: the sentence a use of a name warns with, read without warning."""

from __future__ import annotations

import warnings

import pytest

import untaped.sdk
from untaped.sdk import UntapedDeprecationWarning, deprecated, size_text
from untaped.sdk.marks import old_names
from untaped.sdk.testing import deprecation_of

_SENTENCE = "is deprecated and will be removed in 14.0; use `size_text`"


@pytest.fixture
def renamed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The root gains a deprecated ``human_size`` and an old name ``size_words``."""

    @deprecated(removed_in=14, replacement=size_text)
    @warnings.deprecated(f"human_size {_SENTENCE}", category=UntapedDeprecationWarning)
    def human_size(size: int) -> str:
        return size_text(size)

    human_size.__module__ = "untaped.sdk._exports"
    monkeypatch.setattr("untaped.sdk._exports.human_size", human_size, raising=False)
    monkeypatch.setattr(
        "untaped.sdk._exports.__all__", [*untaped.sdk._exports.__all__, "human_size"]
    )
    table = old_names(size_words=deprecated(removed_in=14, replacement=size_text))
    monkeypatch.setattr("untaped.sdk._exports.OLD_NAMES", table)


@pytest.mark.usefixtures("renamed")
@pytest.mark.parametrize("name", ["human_size", "size_words"])
def test_a_deprecated_or_old_name_gives_its_sentence_and_warns_nothing(name: str) -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        sentence = deprecation_of("untaped.sdk", name)
    assert sentence == f"`untaped.sdk.{name}` {_SENTENCE}"
    assert caught == []
    assert name not in vars(untaped.sdk)


@pytest.mark.usefixtures("renamed")
def test_the_module_may_be_given_as_a_module() -> None:
    assert deprecation_of(untaped.sdk, "size_words") == f"`untaped.sdk.size_words` {_SENTENCE}"


def test_a_current_name_gives_none() -> None:
    assert deprecation_of("untaped.sdk", "size_text") is None


def test_a_submodule_gives_none() -> None:
    assert deprecation_of("untaped.sdk", "marks") is None


def test_a_name_the_module_lacks_raises() -> None:
    with pytest.raises(AttributeError):
        deprecation_of("untaped.sdk", "no_such_name")
