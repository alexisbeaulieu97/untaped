"""A provider may take fewer parameters than its contract method: core passes it its own.

A provider released against an older owner still answers when the owner adds
an optional parameter: each call is bound to the contract method's signature,
its defaults applied, and the provider gets only the names it declares, by
keyword. A parameter the contract can't pass it by name quarantines that
provider alone (``uses-missing``).
"""

from __future__ import annotations

import inspect
from abc import abstractmethod
from datetime import timedelta
from typing import Any, ClassVar

import pytest

from core_test_support.contracts.support import (
    Book,
    BookSource,
    Shop,
    compose,
    plugin,
    shelf_plugin,
    shop_plugin,
)
from untaped.runtime import bootstrap
from untaped.runtime.contracts.registry import Quarantined, doctor_rows, every_offer
from untaped.sdk import Issued, Record
from untaped.sdk.contracts import (
    Contract,
    Errored,
    NoProviderReady,
    Ok,
    Provider,
    Skipped,
    cached,
    gather,
)
from untaped.sdk.contracts._declare import contract_of, parameter_problems
from untaped.sdk.testing import assert_fills, compose_with


class ByName(BookSource, Provider):
    """Released when ``by_author`` took only ``author``."""

    asked: ClassVar[list[dict[str, Any]]] = []

    def books(self) -> list[Book]:
        return []

    def by_author(self, author: str) -> list[Book]:
        type(self).asked.append({"author": author})
        return [Book(title=author)]


class ByLimit(BookSource, Provider):
    """Reads ``limit`` alone, by keyword."""

    asked: ClassVar[list[dict[str, Any]]] = []

    def books(self) -> list[Book]:
        return []

    def by_author(self, *, limit: int) -> list[Book]:
        type(self).asked.append({"limit": limit})
        return [Book(title=str(limit))]


class Everything(BookSource, Provider):
    asked: ClassVar[list[dict[str, Any]]] = []

    def books(self) -> list[Book]:
        return []

    def by_author(self, **kwargs: Any) -> list[Book]:
        type(self).asked.append(kwargs)
        return []


@pytest.fixture(autouse=True)
def _fresh() -> None:
    for cls in (ByName, ByLimit, Everything):
        cls.asked = []


def _answers(provider: type, *args: object, **kwargs: object) -> list[Any]:
    compose(shelf_plugin(), plugin("reader", fills=(provider,), module=__name__))
    return gather(BookSource.by_author)(*args, **kwargs)


@pytest.mark.parametrize(
    ("args", "kwargs"),
    [(("Herbert", 3), {}), (("Herbert",), {"limit": 3}), ((), {"author": "Herbert", "limit": 3})],
    ids=["positional", "mixed", "keyword"],
)
def test_a_provider_with_fewer_parameters_gets_only_its_own(
    args: tuple[object, ...], kwargs: dict[str, object]
) -> None:
    [answer] = _answers(ByName, *args, **kwargs)
    assert isinstance(answer, Ok)
    assert ByName.asked == [{"author": "Herbert"}]


def test_the_contract_s_defaults_are_applied() -> None:
    _answers(ByLimit, "Herbert")
    assert ByLimit.asked == [{"limit": 10}]  # by_author's own default, passed by name


def test_a_provider_taking_keywords_gets_everything() -> None:
    _answers(Everything, "Herbert")
    assert Everything.asked == [{"author": "Herbert", "limit": 10}]


def test_the_cache_keys_by_the_contract_s_arguments() -> None:
    """``limit`` reaches no provider here, yet each value is its own entry, as the contract says."""
    compose(shelf_plugin(), plugin("reader", fills=(ByName,), module=__name__))
    ask = gather(BookSource.by_author)
    ask("Herbert")
    ask("Herbert", limit=10)
    ask("Herbert", 3)
    assert ByName.asked == [{"author": "Herbert"}, {"author": "Herbert"}]  # limit 10, then 3


def test_an_adapted_method_reads_as_the_contract_s() -> None:
    contract = contract_of(BookSource)
    assert contract is not None
    expected = inspect.signature(contract.methods["by_author"].function)
    assert inspect.signature(ByName.by_author) == expected
    assert parameter_problems(ByName, contract) == []


def test_a_provider_with_the_contract_s_parameters_is_not_wrapped() -> None:
    assert Shop.__dict__["by_author"].__wrapped__.__qualname__ == "Shop.by_author"


class Renamed(BookSource, Provider):
    """Renamed ``title`` to quiet a linter."""

    def books(self) -> list[Book]:
        return [Book(title="Dune")]

    def lookup(self, _title: str) -> list[Book]:
        return []


class Positional(BookSource, Provider):
    def books(self) -> list[Book]:
        return [Book(title="Dune")]

    def lookup(self, title: str, /) -> list[Book]:
        return []


@pytest.mark.parametrize(
    ("provider", "problem"),
    [
        (Renamed, "Renamed.lookup takes '_title', which the contract method doesn't declare"),
        (Positional, "Positional.lookup takes 'title' positional-only, and it is passed by name"),
    ],
    ids=["unknown", "positional-only"],
)
def test_a_parameter_the_contract_cannot_pass_quarantines_that_provider_alone(
    provider: type, problem: str
) -> None:
    compose(
        shelf_plugin(),
        shop_plugin(),
        plugin("tagger", offers=(TagSource,), records=(Tag,), module=__name__),
        plugin("reader", fills=(provider, OldTags), module=__name__),
    )
    [quarantined] = [entry for entry in every_offer() if isinstance(entry, Quarantined)]
    assert (quarantined.plugin, quarantined.reason) == ("reader", "uses-missing")
    assert quarantined.detail == (
        f"{problem} (BookSource.lookup takes title): untaped-reader may need a newer "
        "untaped-shelf than it declares"
    )
    assert quarantined.fix == "upgrade untaped-shelf, or upgrade untaped-reader"
    Shop.rows = [Book(title="Dune")]
    answers = {answer.plugin: answer for answer in gather(BookSource.books)()}
    assert isinstance(answers["shop"], Ok)
    assert "reader" not in answers or not isinstance(answers["reader"], Ok)
    [tags] = gather(TagSource.tags)("x-")  # the plugin's other provider still answers
    assert isinstance(tags, Ok) and tags.plugin == "reader"
    assert "reader" in [each.name for each in bootstrap.composition().plugins]
    [row] = [row for row in doctor_rows() if row.title == "uses-missing"]
    assert row.detail.endswith(
        "untaped-shelf than it declares; upgrade untaped-shelf, or upgrade untaped-reader"
    )


def test_a_ranked_provider_set_aside_names_the_fix(monkeypatch: pytest.MonkeyPatch) -> None:
    compose(shelf_plugin(), shop_plugin(), plugin("reader", fills=(Renamed,), module=__name__))
    monkeypatch.setattr("untaped.sdk.contracts._gather.ranking", lambda *_: ("reader",))
    answer = {each.plugin: each for each in gather(BookSource.books)()}["reader"]
    assert isinstance(answer, Errored) and answer.reason == "uses-missing"
    assert answer.error.hint == "upgrade untaped-shelf, or upgrade untaped-reader"


def test_an_unranked_provider_set_aside_names_the_fix() -> None:
    compose(shelf_plugin(), shop_plugin(), plugin("reader", fills=(Renamed,), module=__name__))
    answer = {each.plugin: each for each in gather(BookSource.books)()}["reader"]
    assert isinstance(answer, Skipped) and answer.reason == "uses-missing"
    assert answer.detail.endswith("; upgrade untaped-shelf, or upgrade untaped-reader")


def test_no_provider_ready_names_the_fix() -> None:
    compose(shelf_plugin(), plugin("reader", fills=(Renamed,), module=__name__))
    with pytest.raises(NoProviderReady, match="upgrade untaped-shelf, or upgrade untaped-reader"):
        gather(BookSource.books)()


def test_assert_fills_names_the_fix() -> None:
    with (
        compose_with(shelf_plugin(), plugin("reader", fills=(Renamed,), module=__name__)),
        pytest.raises(AssertionError, match=r"; upgrade untaped-shelf, or upgrade untaped-reader$"),
    ):
        assert_fills(Renamed, samples=[Book(title="Dune")])


def test_a_provider_taking_only_positional_extras_is_refused() -> None:
    """``*args`` would get nothing: every argument is passed by name."""

    class Delegating(BookSource, Provider):
        def books(self) -> list[Book]:
            return []

        def lookup(self, *args: str) -> list[Book]:
            return []

    class Forwarding(BookSource, Provider):
        def books(self) -> list[Book]:
            return []

        def lookup(self, *args: str, **kwargs: str) -> list[Book]:
            return []

    contract = contract_of(BookSource)
    assert contract is not None
    [problem] = parameter_problems(Delegating, contract)
    assert "lookup takes *args without **kwargs, and every argument is passed by name" in problem
    assert parameter_problems(Forwarding, contract) == []


def test_a_subclass_that_fills_the_method_again_answers_for_itself() -> None:
    class Fixed(Renamed):
        def lookup(self, title: str) -> list[Book]:
            return []

    contract = contract_of(BookSource)
    assert contract is not None
    assert parameter_problems(Fixed, contract) == []
    assert parameter_problems(type("Kept", (Renamed,), {}), contract) == [
        "Renamed.lookup takes '_title', which the contract method doesn't declare "
        "(BookSource.lookup takes title)"
    ]


def test_a_mixin_s_method_is_named_where_its_code_is() -> None:
    class Lookups:
        def lookup(self, _title: str) -> list[Book]:
            return []

    class Mixed(Lookups, BookSource, Provider):
        def books(self) -> list[Book]:
            return []

    contract = contract_of(BookSource)
    assert contract is not None
    [problem] = parameter_problems(Mixed, contract)
    assert problem.endswith(
        "Lookups.lookup takes '_title', which the contract method doesn't declare "
        "(BookSource.lookup takes title)"
    )


class Tag(Issued):
    title: str


class TagSource[T: Record = Tag](Contract):
    """Tags, from an owner that added an optional parameter later."""

    def to_tag(self, item: T) -> Tag:
        raise NotImplementedError

    @cached(max_age=timedelta(hours=1), label="tags")
    @abstractmethod
    def tags(self, prefix: str = "", *, limit: int = 5) -> list[Tag]: ...


class OldTags(TagSource, Provider):
    """Released before ``limit``; its bridge takes the item by name only."""

    calls: ClassVar[int] = 0

    def to_tag(self, *, item: Tag) -> Tag:
        return item

    def tags(self, prefix: str) -> list[Tag]:
        type(self).calls += 1
        return [self.to_tag(Tag(title=f"{prefix}tag"))]  # type: ignore[call-arg]  # as called


def test_the_adapter_stacks_with_a_bridge_and_a_cache() -> None:
    contract = contract_of(TagSource)
    assert contract is not None and contract.methods["to_tag"].bridge
    compose(
        plugin("tagger", offers=(TagSource,), records=(Tag,), module=__name__),
        plugin("old", fills=(OldTags,), module=__name__),
    )
    OldTags.calls = 0
    ask = gather(TagSource.tags)
    [first] = ask("x-", limit=2)
    [again] = ask("x-", limit=2)
    assert isinstance(first, Ok) and isinstance(again, Ok)
    assert [tag.title for tag in first.value] == ["x-tag"]
    assert first.value[0].source is not None and first.value[0].source.plugin == "old"
    assert OldTags.calls == 1  # the second ask is the cached answer
    ask("x-", limit=3)
    assert OldTags.calls == 2  # another limit, another entry, though the provider ignores it
