"""Old names: a renamed SDK name keeps its old spelling, as the new object, until it goes.

The root and each group resolve an old name on every lookup (never keeping it),
warn through :func:`untaped.sdk.marks.notify_deprecation`, keep it out of
``__all__``, and the surface lists it under the old name, deprecated. A
contract method filled by its old name fills the new one.
"""

from __future__ import annotations

import sys
import types
import warnings
from abc import abstractmethod
from collections.abc import Iterator, Mapping
from typing import Any

import pytest

import untaped.sdk
from core_test_support.contracts.support import compose, plugin
from untaped.core.surface._sdk import collect_exports, module_lines
from untaped.sdk import Issued, size_text
from untaped.sdk._lazy import OLD_NAMES_ATTR, lazy_names, old_names_of
from untaped.sdk.contracts import Contract, Ok, Provider, gather
from untaped.sdk.marks import Deprecated, deprecated, old_names


@pytest.fixture
def notices(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Each call reaching the warning path, in order."""
    calls: list[dict[str, Any]] = []

    def record(id: str, subject: str, **fields: Any) -> None:
        calls.append({"id": id, "subject": subject, **fields})

    monkeypatch.setattr("untaped.sdk.marks.notify_deprecation", record)
    return calls


# --- the table ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mark",
    [
        deprecated(removed_in=14, replacement="size_text"),  # text names nothing to hand back
        deprecated(replacement=size_text),  # no date
        deprecated(removed_in=14),  # no replacement
        "size_text",
    ],
    ids=["text", "undated", "no-replacement", "not-a-mark"],
)
def test_an_old_name_is_a_dated_mark_naming_the_new_object(mark: object) -> None:
    with pytest.raises(TypeError, match=r"old_names\(size_words=\.\.\.\) takes deprecated"):
        old_names(size_words=mark)  # type: ignore[arg-type]


def test_the_table_is_read_only() -> None:
    table = old_names(size_words=deprecated(removed_in=14, replacement=size_text))
    with pytest.raises(TypeError):
        table["other"] = table["size_words"]  # type: ignore[index]


# --- the root -----------------------------------------------------------------------------


@pytest.fixture
def root_renamed(monkeypatch: pytest.MonkeyPatch) -> None:
    """``untaped.sdk.size_words``, the old spelling of ``size_text``, going in 14.0."""
    table = old_names(size_words=deprecated(removed_in=14, replacement=size_text))
    monkeypatch.setattr("untaped.sdk._exports.OLD_NAMES", table)


@pytest.mark.usefixtures("root_renamed")
def test_a_root_old_name_is_the_new_object_and_warns_on_every_lookup(
    notices: list[dict[str, Any]],
) -> None:
    assert untaped.sdk.size_words is size_text  # type: ignore[attr-defined]
    assert notices == [
        {
            "id": "sdk untaped.sdk.size_words",
            "subject": "`untaped.sdk.size_words`",
            "removed_in": 14,
            "replacement": "`size_text`",
        }
    ]
    from untaped.sdk import size_words  # type: ignore[attr-defined]

    assert size_words is size_text
    # a second plugin reaches the warning path too; notify_deprecation shows it once per run
    assert len(notices) > 1
    assert all(notice == notices[0] for notice in notices)
    assert "size_words" not in vars(untaped.sdk)
    assert "size_words" not in untaped.sdk.__all__
    assert "size_words" not in dir(untaped.sdk)
    assert old_names_of(untaped.sdk) == {
        "size_words": deprecated(removed_in=14, replacement=size_text)
    }


def test_the_root_s_old_names_are_empty_in_12() -> None:
    assert old_names_of(untaped.sdk) == {}


def test_an_unknown_root_name_is_still_an_attribute_error() -> None:
    with pytest.raises(AttributeError, match="has no attribute 'nope'"):
        _ = untaped.sdk.nope  # type: ignore[attr-defined]


# --- a group ------------------------------------------------------------------------------


class _Group:
    """A group's globals and the hooks :func:`lazy_names` gives it."""

    def __init__(self, old: Mapping[str, Deprecated]) -> None:
        self.built = 0
        self.namespace: dict[str, object] = {
            "__name__": "untaped.sdk.sample",
            "__all__": ["size_text"],
        }

        def table() -> Mapping[str, Deprecated]:
            self.built += 1
            return old

        self.getattr, self.dir = lazy_names(
            self.namespace, {"untaped.sdk.words": ("size_text",)}, old=table
        )


def test_a_group_builds_its_table_on_the_first_name_it_does_not_list() -> None:
    group = _Group(old_names(size_words=deprecated(removed_in=14, replacement=size_text)))
    assert group.built == 0  # importing the group loads nothing
    assert group.getattr("size_text") is size_text
    assert group.built == 0
    assert group.getattr("size_words") is size_text
    assert group.getattr("size_words") is size_text
    assert group.built == 1


def test_a_group_s_old_name_warns_on_every_lookup_and_is_never_kept(
    notices: list[dict[str, Any]],
) -> None:
    group = _Group(old_names(size_words=deprecated(removed_in=14, replacement=size_text)))
    group.getattr("size_words")
    group.getattr("size_words")
    assert [notice["id"] for notice in notices] == 2 * ["sdk untaped.sdk.sample.size_words"]
    assert "size_words" not in group.namespace
    assert "size_words" not in group.dir()
    with pytest.raises(AttributeError, match="has no attribute 'nope'"):
        group.getattr("nope")
    with pytest.raises(AttributeError):
        group.getattr("__path__")  # a dunder never reaches the table


def test_a_group_without_old_names_publishes_an_empty_table() -> None:
    import untaped.sdk.storage as storage

    assert OLD_NAMES_ATTR in vars(storage)
    assert old_names_of(storage) == {}


# --- the surface --------------------------------------------------------------------------


def _new(count: int) -> str:
    return str(count)


_SAMPLE = "untaped_old_names_sample"


@pytest.fixture
def sample() -> Iterator[types.ModuleType]:
    """A module exporting ``_new`` as ``new``, with ``old`` its old spelling."""
    module = types.ModuleType(_SAMPLE)
    module.__all__ = ["new"]  # type: ignore[attr-defined]
    module.new = _new  # type: ignore[attr-defined]
    table = old_names(old=deprecated(removed_in=14, replacement=_new))
    setattr(module, OLD_NAMES_ATTR, lambda: table)
    sys.modules[_SAMPLE] = module
    try:
        yield module
    finally:
        sys.modules.pop(_SAMPLE, None)


@pytest.mark.usefixtures("sample")
def test_an_old_name_is_a_deprecated_surface_line_under_its_old_spelling() -> None:
    exports = collect_exports({_SAMPLE: None})
    lines = sorted(str(line) for line in module_lines(_SAMPLE, None, exports))
    assert lines == [
        f"sdk {_SAMPLE}.new(count)",
        f"sdk {_SAMPLE}.old(count)  [deprecated, removed in 14.0; use _new]",
    ]


# --- a contract method --------------------------------------------------------------------


class Item(Issued):
    name: str


class ItemSource(Contract):
    """Items, from an owner that renamed ``items`` to ``all_items``."""

    @abstractmethod
    def all_items(self) -> list[Item]: ...

    @deprecated(removed_in=14, replacement=all_items)
    @warnings.deprecated("ItemSource.items is deprecated; use `all_items`")
    def items(self) -> list[Item]:
        raise NotImplementedError


class OldShop(ItemSource, Provider):
    """Released before the rename: it fills ``items``."""

    def items(self) -> list[Item]:
        return [Item(name="old")]


class NewShop(ItemSource, Provider):
    def all_items(self) -> list[Item]:
        return [Item(name="new")]


class BothShop(ItemSource, Provider):
    """Fills both: the new name is the one asked."""

    def items(self) -> list[Item]:
        return [Item(name="old")]

    def all_items(self) -> list[Item]:
        return [Item(name="new")]


def test_a_provider_filling_a_renamed_method_by_its_old_name_answers_the_new_one() -> None:
    compose(
        plugin("owner", offers=(ItemSource,), records=(Item,), module=__name__),
        plugin("old", fills=(OldShop,), module=__name__),
        plugin("new", fills=(NewShop,), module=__name__),
        plugin("both", fills=(BothShop,), module=__name__),
    )
    answers = gather(ItemSource.all_items)()
    assert all(isinstance(answer, Ok) for answer in answers), answers
    names = {
        answer.plugin: [item.name for item in answer.value]
        for answer in answers
        if isinstance(answer, Ok)
    }
    assert names == {"old": ["old"], "new": ["new"], "both": ["new"]}
