"""Declaring a contract and filling it (design §3, §6): checks at declaration and the rewrap."""

from __future__ import annotations

from abc import abstractmethod
from datetime import timedelta
from typing import Any

import pytest
from pydantic import BaseModel, SecretStr

from test_contracts.support import (
    LIBRARY_CONFIG,
    Book,
    BookSource,
    Library,
    Shop,
    Volume,
    compose,
    library_spec,
    shelf_spec,
    write_config,
)
from untaped.contracts import Contract, Ok, Record, bridge, cached, gather
from untaped.contracts._declare import ContractError, contract_of, item_type
from untaped.plugins.registry import PluginSpec
from untaped.stability import Experimental, deprecated, function_mark


def test_a_contract_is_declared_with_its_name_methods_and_owner_model() -> None:
    info = contract_of(BookSource)
    assert info is not None
    assert info.name == "book_source"
    assert info.item is Book
    assert set(info.methods) == {"to_book", "books", "lookup", "by_author", "first", "count"}
    assert info.methods["to_book"].bridge
    assert info.methods["books"].ttl == timedelta(hours=1)
    assert info.methods["books"].listing
    assert not info.methods["lookup"].listing
    assert isinstance(function_mark(BookSource), Experimental)


@pytest.mark.parametrize("name", ["list", "id", "settings", "ready", "http", "gather"])
def test_a_method_may_not_shadow_a_builtin_or_sdk_name(name: str) -> None:
    def method(self: Any) -> list[int]:
        raise NotImplementedError

    with pytest.raises(TypeError, match=f"{name}: a contract method may not be named"):
        type("Bad", (Contract,), {name: method})


class _Opaque:
    pass


def test_every_parameter_and_return_must_be_serialisable() -> None:
    with pytest.raises(ContractError, match="unserialisable-signature"):

        class Untyped(Contract):
            def find(self, name) -> list[Book]:  # type: ignore[no-untyped-def]  # the point
                raise NotImplementedError

    with pytest.raises(ContractError, match=r"\*args"):

        class Star(Contract):
            def find(self, *names: str) -> list[Book]:
                raise NotImplementedError

    with pytest.raises(ContractError, match="positional-only"):

        class Positional(Contract):
            def find(self, name: str, /) -> list[Book]:
                raise NotImplementedError

    with pytest.raises(ContractError, match="is not JSON"):

        class Sentinel(Contract):
            def find(self, name: str = _Opaque()) -> list[Book]:  # type: ignore[assignment]
                raise NotImplementedError

    with pytest.raises(ContractError, match="has no JSON schema"):

        class Opaque(Contract):
            def find(self, name: _Opaque) -> list[Book]:
                raise NotImplementedError

    with pytest.raises(ContractError, match="annotate the return type"):

        class NoReturn(Contract):
            def find(self, name: str):  # type: ignore[no-untyped-def]  # the point
                raise NotImplementedError


class _Credential(BaseModel):
    token: SecretStr


def test_cached_is_refused_on_a_method_returning_a_secret() -> None:
    with pytest.raises(TypeError, match="never cached"):

        class Host(Contract):
            @cached(ttl=timedelta(minutes=5))
            def credential(self, url: str) -> _Credential:
                raise NotImplementedError


def test_the_item_parameter_needs_the_owner_model_as_default() -> None:
    with pytest.raises(TypeError, match="needs the owner's model as its default"):

        class Bare[T: Record](Contract):
            def to_x(self, item: T) -> Book:
                raise NotImplementedError


def test_shell_is_a_reserved_contract_keyword() -> None:
    class Piped(Contract, shell=True):
        def names(self) -> list[str]:
            raise NotImplementedError

    info = contract_of(Piped)
    assert info is not None
    assert info.shell
    with pytest.raises(TypeError, match="shell= is a contract's keyword"):

        class Provider(Piped, shell=True):  # type: ignore[call-arg]  # the point
            def names(self) -> list[str]:
                return []


def test_a_provider_override_is_rewrapped_with_the_owner_decorators() -> None:
    assert getattr(Library.to_book, "__untaped_bridge__", None) is not None
    assert getattr(Library.books, "__untaped_cached__", None) == timedelta(hours=1)
    assert getattr(Shop.books, "__untaped_cached__", None) == timedelta(hours=1)


def test_the_bridge_stamps_source_even_inside_the_provider() -> None:
    compose(shelf_spec(), library_spec())
    write_config(LIBRARY_CONFIG)
    [answer] = gather(BookSource.books)()
    books = answer.value  # type: ignore[union-attr]  # an Ok
    assert [book.source.record for book in books] == [  # type: ignore[union-attr]
        {"id": 1, "name": "Dune"},
        {"id": 2, "name": "Emma"},
    ]
    assert {book.source.kind for book in books} == {"library.volume"}  # type: ignore[union-attr]


def test_the_item_type_resolves_from_the_direct_base_or_the_default() -> None:
    info = contract_of(BookSource)
    assert info is not None
    assert item_type(Library, info) is Volume
    assert item_type(Shop, info) is Book

    class Paged[U: Record](BookSource[U]):
        pass

    class Indirect(Paged[Volume]):
        def books(self) -> list[Book]:
            return []

    with pytest.raises(ContractError, match="unresolved-item-type"):
        item_type(Indirect, info)

    class Kindless(Record):
        name: str

    class NoKind(BookSource[Kindless]):
        def to_book(self, item: Kindless) -> Book:
            return Book(title=item.name)

        def books(self) -> list[Book]:
            return []

    with pytest.raises(ContractError, match="declares no kind"):
        item_type(NoKind, info)


def test_a_required_method_must_be_filled() -> None:
    class Lazy(BookSource):
        pass

    with pytest.raises(TypeError, match="abstract"):
        Lazy()  # type: ignore[abstract]  # the point


def test_a_bridge_used_outside_the_registry_says_how_to_get_a_provider() -> None:
    from untaped.errors import UntapedError

    with pytest.raises(UntapedError, match="not bound to a plugin"):
        Library().to_book(Volume(id=1, name="Dune"))


def test_bridge_and_cached_keep_the_method_signature() -> None:
    class Direct(Contract):
        @bridge
        def make(self, item: Volume) -> Book:
            raise NotImplementedError

        @cached(ttl=timedelta(seconds=1))
        @abstractmethod
        def listing(self) -> list[Book]: ...

    import inspect

    assert list(inspect.signature(Direct.make).parameters) == ["self", "item"]
    assert getattr(Direct.listing, "__isabstractmethod__", False)
    with pytest.raises(TypeError, match="positive"):
        cached(ttl=timedelta(0))


def test_only_the_contract_decides_what_is_cached() -> None:
    with pytest.raises(TypeError, match="only the contract decides what is @cached"):

        class Eager(Shop):
            @cached(ttl=timedelta(minutes=1))
            def _load(self) -> list[Book]:
                return []

    with pytest.raises(TypeError, match=r"Hasty\.books: only the contract decides"):

        class Hasty(Shop):
            @cached(ttl=timedelta(seconds=1))
            def books(self) -> list[Book]:
                return []


def test_a_renamed_method_keeps_its_old_name_marked_deprecated() -> None:
    from untaped.stability import Deprecated

    class Catalogue(Contract):
        def entries(self) -> list[Book]:
            raise NotImplementedError

        @deprecated(replacement="entries")
        def items(self) -> list[Book]:
            raise NotImplementedError

    info = contract_of(Catalogue)
    assert info is not None
    assert set(info.methods) == {"entries", "items"}
    mark = function_mark(Catalogue.items)
    assert isinstance(mark, Deprecated)
    assert mark.replacement == "entries"


def test_a_method_filled_under_another_name_is_cached_as_the_contract_method() -> None:
    class Aliased(Shop):
        def _load(self) -> list[Book]:
            return [Book(title="Dune")]

        books = _load

    compose(shelf_spec(), PluginSpec(name="shop", provides={"shelf": lambda: (Aliased(),)}))
    [answer] = gather(BookSource.books)()
    assert isinstance(answer, Ok)
    assert [book.title for book in answer.value] == ["Dune"]


def test_a_method_filled_by_a_plain_mixin_is_rewrapped_too() -> None:
    class Shared:
        def books(self) -> list[Book]:
            return [Book(title="Dune")]

    class Mixed(Shared, Shop):
        pass

    assert getattr(Mixed.books, "__untaped_cached__", None) == timedelta(hours=1)

    class Cached:
        @cached(ttl=timedelta(days=30))
        def books(self) -> list[Book]:
            return []

    with pytest.raises(TypeError, match=r"Cached\.books: only the contract decides"):

        class Overridden(Cached, Shop):
            pass


def test_an_alias_of_an_inherited_cached_method_is_not_a_provider_cache() -> None:
    class Again(Shop):
        latest = Shop.books

    assert Again.latest is Shop.books
