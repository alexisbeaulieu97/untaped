"""``compose_with``, ``assert_fills`` and ``assert_surface`` from ``untaped.sdk.testing``."""

from __future__ import annotations

import math
from abc import abstractmethod
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Annotated

import pytest
from pydantic import BaseModel

from core_test_support.contracts.support import (
    Book,
    BookSource,
    Kiosk,
    Library,
    Shop,
    Volume,
    compose,
    fresh_providers,
    library_spec,
    shelf_spec,
)
from untaped.runtime import bootstrap
from untaped.runtime.registry import PluginCandidate, PluginSpec
from untaped.sdk import Plugin, deprecated, experimental
from untaped.sdk.contracts import Contract, Issued, Ok, Provider, Record, Source, cached, gather
from untaped.sdk.storage import Cache
from untaped.sdk.testing import assert_fills, assert_surface, compose_with


@pytest.fixture(autouse=True)
def _fresh_providers() -> Iterator[None]:
    with fresh_providers():
        yield


def test_compose_with_offers_a_fake_to_the_owner_and_restores_the_composition() -> None:
    before = compose(shelf_spec())
    Kiosk.rows = [Book(title="Dune")]
    with compose_with(shelf_spec(), provides={"fake": [Kiosk()]}) as result:
        assert [plugin.spec.name for plugin in result.plugins] == ["untaped", "fake", "shelf"]
        [answer] = gather(BookSource.books, refresh=True)()
        assert isinstance(answer, Ok)
        assert answer.plugin == "fake"
        assert [book.title for book in answer.value] == ["Dune"]
        assert answer.value[0].source == Source(plugin="fake", kind="shelf.book")
    assert bootstrap.composition() is before


def test_compose_with_refuses_what_it_cannot_compose() -> None:
    with pytest.raises(LookupError, match="no installed plugin named 'nowhere'"):
        compose_with("nowhere").__enter__()
    with pytest.raises(LookupError, match="no composed plugin declares BookSource"):
        compose_with(PluginSpec(name="other"), provides={"fake": [Kiosk()]}).__enter__()
    with pytest.raises(TypeError, match="is not a provider"):
        compose_with(shelf_spec(), provides={"fake": [object()]}).__enter__()  # type: ignore[list-item]


def test_assert_fills_passes_for_a_bridging_provider() -> None:
    with compose_with(shelf_spec(), library_spec()):
        assert_fills(Library, samples=[Volume(id=1, name="Dune"), {"id": 2, "name": "Emma"}])


def test_assert_fills_needs_samples_when_the_provider_has_its_own_records() -> None:
    with (
        compose_with(shelf_spec(), library_spec()),
        pytest.raises(TypeError, match="issues Volume records, so assert_fills needs samples"),
    ):
        assert_fills(Library())


def test_assert_fills_of_the_owners_model_needs_no_samples() -> None:
    spec = PluginSpec(name="shop", provides={"shelf": lambda: (Shop(),)})
    with compose_with(shelf_spec(), spec):
        assert_fills(Shop)
        assert_fills(Shop, samples=[Book(title="Dune")])


class Leaky(Volume, kind="library.leaky"):
    """A record whose NaN ratio is written as JSON null: it can't round-trip."""

    ratio: float = 0.0


class Forger(BookSource[Leaky]):
    def to_book(self, item: Leaky) -> Book:
        if item.name == "blank":
            return Book.model_construct(title=None)  # skips validation: an invalid Book
        return Book(title=item.name)

    def books(self) -> list[Book]:
        return []


def test_assert_fills_lists_every_problem() -> None:
    spec = PluginSpec(name="forge", provides={"shelf": lambda: (Forger(),)})
    with compose_with(shelf_spec(), spec), pytest.raises(AssertionError) as failed:
        assert_fills(
            Forger,
            samples=[
                Leaky(id=1, name="Dune", ratio=math.nan),
                {"id": "x"},
                {"id": 3, "name": "blank"},
            ],
        )
    message = str(failed.value)
    assert "Forger doesn't fill book_source" in message
    assert "sample 1 doesn't read back from JSON" in message
    assert "to_book(sample 3): title: Input should be a valid string" in message
    assert "sample 2 is not a Leaky" in message


def test_assert_fills_names_a_quarantined_offer() -> None:
    class Unbridged(BookSource[Volume]):
        def books(self) -> list[Book]:
            return []

    spec = PluginSpec(name="lost", provides={"shelf": lambda: (Unbridged(),)})
    with (
        compose_with(shelf_spec(), spec),
        pytest.raises(
            AssertionError, match=r"lost's offer to shelf is quarantined \(missing-bridge\)"
        ),
    ):
        assert_fills(Unbridged, samples=[Volume(id=1, name="Dune")])


def test_assert_fills_asks_for_an_owner_that_is_not_composed() -> None:
    lost = PluginSpec(name="aaa", provides={"elsewhere": lambda: ()})
    with (
        compose_with(lost, library_spec()),
        pytest.raises(LookupError, match=r"Library fills book_source, whose owner isn't composed"),
    ):
        assert_fills(Library, samples=[Volume(id=1, name="Dune")])


def test_assert_fills_refuses_a_contract_and_an_uncomposed_provider() -> None:
    with pytest.raises(TypeError, match="BookSource is not a provider"):
        assert_fills(BookSource)  # type: ignore[type-abstract]
    with pytest.raises(LookupError, match="no installed plugin offers Library"):
        assert_fills(Library, samples=[Volume(id=1, name="Dune")])


class Item(Issued, kind="shelf.item"):
    name: str
    size: int = 0


def _contract(variant: str, *, mark: bool = False) -> type[Contract]:
    """``ItemSource`` as it reads in each test's version of the owner."""
    if variant == "v1":

        class ItemSource[T: Record = Item](Contract):
            def to_item(self, item: T) -> Item:
                raise NotImplementedError

            @abstractmethod
            def items(self, prefix: str = "") -> list[Item]: ...

        found: type[Contract] = ItemSource
    elif variant == "added":

        class ItemSource[T: Record = Item](Contract):  # type: ignore[no-redef]
            """Now documented, with a new optional method."""

            def to_item(self, item: T) -> Item:
                raise NotImplementedError

            @abstractmethod
            def items(self, prefix: str = "") -> list[Item]: ...

            def first(self) -> Item:
                raise NotImplementedError

        found = ItemSource
    else:

        class ItemSource[T: Record = Item](Contract):  # type: ignore[no-redef]
            def to_item(self, item: T) -> Item:
                raise NotImplementedError

            @abstractmethod
            def items(self, prefix: int, wanted: str) -> list[Item]: ...

        found = ItemSource
    return experimental(found) if mark else found


def _owner(contract: type[Contract]) -> PluginSpec:
    return PluginSpec(name="items", contracts=lambda: (contract,))


def test_assert_surface_writes_the_file_then_keeps_it(tmp_path: Path) -> None:
    path = tmp_path / "surface.txt"
    assert_surface(_owner(_contract("v1")), path)
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# untaped surface 1\n")
    for line in (
        "contract items.item_source abstract=items",
        'contract items.item_source.items(prefix: string = "") -> Item[]',
        "contract items.item_source.to_item(item: Item) -> Item",
        "contract items.Item(name)",
        "contract items.Item.size integer default=0",
        "cfg items.extensions.item_source.rank.items string[] default=[]",
    ):
        assert f"\n{line}\n" in text
    assert_surface(_owner(_contract("v1")), path)
    assert path.read_text(encoding="utf-8") == text


def test_assert_surface_rewrites_the_file_after_an_addition(tmp_path: Path) -> None:
    path = tmp_path / "surface.txt"
    assert_surface(_owner(_contract("v1")), path)
    assert_surface(_owner(_contract("added")), path)
    assert "contract items.item_source.first() -> Item\n" in path.read_text(encoding="utf-8")


def test_a_removed_line_fails_until_the_file_is_deleted(tmp_path: Path) -> None:
    path = tmp_path / "surface.txt"
    assert_surface(_owner(_contract("v1")), path)
    with pytest.raises(AssertionError) as failed:
        assert_surface(_owner(_contract("breaking")), path)
    message = str(failed.value)
    assert "delete the file and run again to accept these in a major release" in message
    assert '  contract items.item_source.items(prefix: string = "") -> Item[]' in message
    path.unlink()
    assert_surface(_owner(_contract("breaking")), path)
    assert "items(prefix: integer, wanted: string)" in path.read_text(encoding="utf-8")


def test_removing_experimental_lines_passes(tmp_path: Path) -> None:
    path = tmp_path / "surface.txt"
    assert_surface(_owner(_contract("v1", mark=True)), path)
    assert "abstract=items  [experimental]" in path.read_text(encoding="utf-8")
    assert_surface(_owner(_contract("breaking", mark=True)), path)


class _Undated(BaseModel):
    old: Annotated[int, deprecated(replacement="items.new")] = 0
    new: int = 0


class _Dated(BaseModel):
    old: Annotated[int, deprecated(removed_in=3, replacement="items.new")] = 0
    new: int = 0


def _dated(removed_in: int | None) -> PluginSpec:
    return PluginSpec(name="items", settings=_Undated if removed_in is None else _Dated)


def test_an_undated_file_stays_format_1_and_a_dated_one_is_rewritten(tmp_path: Path) -> None:
    path = tmp_path / "surface.txt"
    assert_surface(_dated(None), path)
    assert path.read_text(encoding="utf-8").startswith("# untaped surface 1\n")
    assert_surface(_dated(3), path)
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# untaped surface 2\n")
    assert "cfg items.old integer default=0  [deprecated, removed in 3.0; use items.new]\n" in text


@pytest.mark.parametrize("version", ["2.3.0", "2.3.1.dev5+g1234"])
def test_with_a_version_a_later_date_passes(tmp_path: Path, version: str) -> None:
    assert_surface(_dated(3), tmp_path / "surface.txt", version=version)


@pytest.mark.parametrize("version", ["3.0.0", "3.0.0rc1", "3.0.0.dev0", "4.1.0"])
def test_with_a_version_an_overdue_line_fails(tmp_path: Path, version: str) -> None:
    with pytest.raises(AssertionError, match=r"marked for \d+\.0 or earlier") as failed:
        assert_surface(_dated(3), tmp_path / "surface.txt", version=version)
    assert "  cfg items.old integer default=0  [deprecated, removed in 3.0" in str(failed.value)


def test_with_a_version_an_undated_line_fails(tmp_path: Path) -> None:
    assert_surface(_dated(None), tmp_path / "surface.txt")  # without a version: as before
    with pytest.raises(AssertionError, match="every deprecation is a promise with a date"):
        assert_surface(_dated(None), tmp_path / "surface.txt", version="2.3.0")


class _ShelfSettings(BaseModel):
    """The toy owner's settings."""


_SHELVED = Cache(label="books", max_age=timedelta(hours=1))


@experimental
class Shelved(Contract):
    """Books on a plugin class's shelf; the cache is the one its owner holds."""

    @cached(_SHELVED)
    @abstractmethod
    def books(self) -> list[Book]: ...


class _Shelf(Plugin):
    """Ask for books."""

    settings = _ShelfSettings
    offers = (Shelved,)
    shelved_books = _SHELVED


class _StockistSettings(BaseModel):
    """The toy provider's settings."""


class _Stocked(Shelved, Provider):
    def books(self) -> list[Book]:
        return [Book(title="Dune")]


class _Stockist(Plugin):
    """Lend books."""

    settings = _StockistSettings
    fills = (_Stocked,)


def test_assert_fills_composes_an_installed_plugin_class_with_the_owner_it_fills(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from untaped.runtime import registry

    def candidate(name: str, cls: type) -> PluginCandidate:
        target = f"{cls.__module__}:{cls.__qualname__}"
        return PluginCandidate(distribution=f"untaped-{name}", name=name, target=target)

    installed = (candidate("stockist", _Stockist), candidate("shelf", _Shelf))
    monkeypatch.setattr(registry, "discover_candidates", lambda: installed)
    assert_fills(_Stocked)
