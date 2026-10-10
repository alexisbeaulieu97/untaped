"""What an answer costs in validation: once per item live, at most twice from the cache.

Counted with a validator on the owner's model, not timed, so the budget holds
on any machine. The provider builds its items with ``model_construct`` (no
validation), so every count is the SDK's own.
"""

from __future__ import annotations

from abc import abstractmethod
from datetime import timedelta
from typing import ClassVar, Self

from pydantic import model_validator

from untaped.contracts import Contract, Issued, Ok, Record, cached, gather
from untaped.plugins.registry import PluginSpec
from untaped.testing import compose_with


class Tally(Issued, kind="tally.item"):
    """An owner record that counts its validations."""

    validations: ClassVar[int] = 0

    name: str

    @model_validator(mode="after")
    def _count(self) -> Self:
        type(self).validations += 1
        return self


class TallySource[T: Record = Tally](Contract):
    @cached(max_age=timedelta(hours=1))
    @abstractmethod
    def items(self) -> list[Tally]: ...


class Counter(TallySource):
    calls: ClassVar[int] = 0

    def items(self) -> list[Tally]:
        type(self).calls += 1
        return [Tally.model_construct(name=name, source=None) for name in ("a", "b", "c")]


def _ask(refresh: bool) -> list[Tally]:
    [answer] = gather(TallySource.items, refresh=refresh)()
    assert isinstance(answer, Ok)
    assert answer.stale is None
    return answer.value


def test_a_live_answer_validates_each_item_once_and_a_cached_one_at_most_twice() -> None:
    owner = PluginSpec(name="tally", contracts=lambda: (TallySource,))
    with compose_with(owner, provides={"fake": [Counter()]}):
        Counter.calls = Tally.validations = 0
        live = _ask(refresh=True)
        assert [item.name for item in live] == ["a", "b", "c"]
        assert Tally.validations == len(live)

        Tally.validations = 0
        cached_items = _ask(refresh=False)
        assert Counter.calls == 1, "the second answer comes from the cache"
        assert cached_items == live
        assert Tally.validations <= 2 * len(cached_items)
