"""A renamed contract method: providers filling the old name answer the new one.

The old method stays on the contract, deprecated with the new method as its
replacement; the owner asks only the new name.
"""

from __future__ import annotations

import warnings
from abc import abstractmethod

from core_test_support.contracts.support import compose, plugin
from untaped.sdk import Issued, Record
from untaped.sdk.contracts import Contract, Ok, Provider, gather
from untaped.sdk.contracts._declare import contract_of
from untaped.sdk.marks import deprecated


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


class SubShop(OldShop):
    """Overrides the old name its base filled."""

    def items(self) -> list[Item]:
        return [Item(name="sub")]


class MovedBase(ItemSource, Provider):
    def all_items(self) -> list[Item]:
        return [Item(name="base")]


class OldSub(MovedBase):
    """Written before its base moved to the new name: it still overrides the old one."""

    def items(self) -> list[Item]:
        return [Item(name="old-sub")]


def _answers(*providers: type) -> dict[str, list[str]]:
    compose(
        plugin("owner", offers=(ItemSource,), records=(Item,), module=__name__),
        *(plugin(f"p{at}", fills=(each,), module=__name__) for at, each in enumerate(providers)),
    )
    answers = gather(ItemSource.all_items)()
    assert all(isinstance(answer, Ok) for answer in answers), answers
    return {
        answer.plugin: [item.name for item in answer.value]
        for answer in answers
        if isinstance(answer, Ok)
    }


def test_a_provider_filling_the_old_name_answers_the_new_one() -> None:
    assert _answers(OldShop, NewShop, BothShop) == {
        "p0": ["old"],
        "p1": ["new"],
        "p2": ["new"],
    }


def test_a_subclass_overriding_the_old_name_is_the_answer() -> None:
    assert _answers(SubShop, OldSub) == {"p0": ["sub"], "p1": ["old-sub"]}


# --- a renamed bridge ----------------------------------------------------------------------


class Tag(Issued):
    title: str


class Raw(Record):
    label: str


class TagSource[T: Record = Tag](Contract):
    """Tags, whose bridge was renamed from ``as_tag`` to ``to_tag``."""

    def to_tag(self, item: T) -> Tag:
        raise NotImplementedError

    @deprecated(removed_in=14, replacement=to_tag)
    @warnings.deprecated("TagSource.as_tag is deprecated; use `to_tag`")
    def as_tag(self, item: T) -> Tag:
        raise NotImplementedError

    @abstractmethod
    def tags(self) -> list[Tag]: ...


class OldTags(TagSource[Raw], Provider):
    """Released before the rename: its bridge is ``as_tag``."""

    def as_tag(self, item: Raw) -> Tag:
        return Tag(title=item.label)

    def tags(self) -> list[Tag]:
        return [self.to_tag(Raw(label="x"))]


def test_a_renamed_bridge_is_one_bridge_and_its_old_name_fills_it() -> None:
    contract = contract_of(TagSource)
    assert contract is not None
    assert (contract.methods["to_tag"].bridge, contract.methods["as_tag"].bridge) == (True, False)
    compose(
        plugin("tagger", offers=(TagSource,), records=(Tag,), module=__name__),
        plugin("old", fills=(OldTags,), module=__name__),
    )
    [answer] = gather(TagSource.tags)()
    assert isinstance(answer, Ok)
    assert [tag.title for tag in answer.value] == ["x"]
