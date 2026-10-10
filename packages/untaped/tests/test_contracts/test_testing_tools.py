"""``compose_with``, ``assert_fills`` and ``assert_contract_schemas`` from ``untaped.testing``."""

from __future__ import annotations

import json
import math
from abc import abstractmethod
from pathlib import Path

import pytest

from test_contracts.support import (
    Book,
    BookSource,
    Kiosk,
    Library,
    Shop,
    Volume,
    compose,
    library_spec,
    shelf_spec,
)
from untaped import bootstrap
from untaped.contracts import Contract, Issued, Ok, Record, Source, _fills, bridge, gather
from untaped.contracts._declare import contract_of
from untaped.contracts._schema import schema_hash
from untaped.plugins.registry import PluginSpec
from untaped.sdk import experimental
from untaped.testing import assert_contract_schemas, assert_fills, compose_with


@pytest.fixture
def fills_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Where ``assert_fills`` records hashes: a temp file, never the test package."""
    path = tmp_path / "fills.json"
    monkeypatch.setattr(_fills, "fills_path", lambda provider: path)
    return path


def test_compose_with_offers_a_fake_to_the_owner_and_restores_the_composition() -> None:
    before = compose(shelf_spec())
    Kiosk.rows = [Book(title="Dune")]
    with compose_with(shelf_spec(), provides={"fake": [Kiosk()]}) as result:
        assert [plugin.spec.name for plugin in result.plugins] == ["fake", "shelf"]
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


def test_assert_fills_passes_for_a_bridging_provider_and_records_the_hash(fills_file: Path) -> None:
    with compose_with(shelf_spec(), library_spec()):
        assert_fills(Library, samples=[Volume(id=1, name="Dune"), {"id": 2, "name": "Emma"}])
    recorded = json.loads(fills_file.read_text(encoding="utf-8"))
    info = contract_of(BookSource)
    assert info is not None
    assert recorded == {"untaped": "1", "fills": {"shelf.book_source": schema_hash(info)}}


def test_assert_fills_needs_samples_when_the_provider_has_its_own_records() -> None:
    with (
        compose_with(shelf_spec(), library_spec()),
        pytest.raises(TypeError, match="issues Volume records, so assert_fills needs samples"),
    ):
        assert_fills(Library())


def test_assert_fills_of_the_owners_model_needs_no_samples(fills_file: Path) -> None:
    spec = PluginSpec(name="shop", provides={"shelf": lambda: (Shop(),)})
    with compose_with(shelf_spec(), spec):
        assert_fills(Shop)
        assert_fills(Shop, samples=[Book(title="Dune")])
    assert fills_file.exists()


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


def test_assert_fills_lists_every_problem(fills_file: Path) -> None:
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
    assert not fills_file.exists()


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


def test_a_package_in_site_packages_keeps_its_fills_even_through_a_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site, cache = tmp_path / "site", tmp_path / "cache"
    (site / "untaped_acme").mkdir(parents=True)
    cache.mkdir()
    (cache / "fills.json").write_text("{}", encoding="utf-8")
    linked = site / "untaped_acme" / "fills.json"
    linked.symlink_to(cache / "fills.json")
    paths = {"purelib": str(site), "platlib": str(site)}
    monkeypatch.setattr(_fills.sysconfig, "get_paths", lambda: paths)
    assert _fills._installed(linked)
    assert not _fills._installed(cache / "fills.json")


def test_assert_fills_refuses_a_contract_and_an_uncomposed_provider() -> None:
    with pytest.raises(TypeError, match="BookSource is not a provider"):
        assert_fills(BookSource)  # type: ignore[type-abstract]
    with pytest.raises(LookupError, match="no installed plugin offers Library"):
        assert_fills(Library, samples=[Volume(id=1, name="Dune")])


class Item(Issued, kind="shelf.item"):
    name: str
    size: int = 0


def _contract(variant: str) -> type[Contract]:
    """``ItemSource`` as it reads in each test's version of the owner."""
    if variant == "v1":

        @experimental
        class ItemSource[T: Record = Item](Contract):
            @bridge
            def to_item(self, item: T) -> Item:
                raise NotImplementedError

            @abstractmethod
            def items(self, prefix: str = "") -> list[Item]: ...

            def count(self) -> int:
                raise NotImplementedError

        return ItemSource
    if variant == "compatible":

        @experimental
        class ItemSource[T: Record = Item](Contract):  # type: ignore[no-redef]
            """Now documented; a new optional parameter and method, ``count`` removed."""

            @bridge
            def to_item(self, item: T) -> Item:
                raise NotImplementedError

            @abstractmethod
            def items(self, prefix: str = "", limit: int = 10) -> list[Item]: ...

            def first(self) -> Item:
                raise NotImplementedError

        return ItemSource

    class ItemSource[T: Record = Item](Contract):  # type: ignore[no-redef]  # now stable
        @bridge
        def to_item(self, item: T) -> Item:
            raise NotImplementedError

        @abstractmethod
        def items(self, prefix: int, wanted: str) -> list[Item]: ...

    return ItemSource


def test_a_contract_snapshot_is_written_then_kept_through_compatible_changes(
    tmp_path: Path,
) -> None:
    assert_contract_schemas(_contract("v1"), snapshots=tmp_path)
    snapshot = json.loads((tmp_path / "item_source.json").read_text(encoding="utf-8"))
    assert snapshot["contract"] == "item_source"
    assert set(snapshot["methods"]) == {"to_item", "items", "count"}
    assert snapshot["methods"]["items"]["stability"] == "experimental"
    assert snapshot["methods"]["items"]["params"]["required"] == []
    assert_contract_schemas(_contract("compatible"), snapshots=tmp_path)
    rewritten = json.loads((tmp_path / "item_source.json").read_text(encoding="utf-8"))
    assert set(rewritten["methods"]) == {"to_item", "items", "first"}


def test_an_incompatible_contract_change_fails_until_the_snapshot_is_deleted(
    tmp_path: Path,
) -> None:
    assert_contract_schemas(_contract("v1"), snapshots=tmp_path)
    with pytest.raises(AssertionError) as failed:
        assert_contract_schemas(_contract("breaking"), snapshots=tmp_path)
    message = str(failed.value)
    assert "item_source.items().prefix: type " in message
    assert "item_source.items().wanted was added as a required parameter" in message
    assert "item_source.items().prefix became required" in message
    (tmp_path / "item_source.json").unlink()
    assert_contract_schemas(_contract("breaking"), snapshots=tmp_path)


def test_removing_a_field_or_a_stable_method_is_incompatible(tmp_path: Path) -> None:
    from untaped.contracts._schema import schema_changes

    old = {
        "methods": {
            "gone": {"stability": "stable", "params": {}, "return": {}},
            "kept": {
                "stability": "stable",
                "params": {"type": "object", "properties": {}, "required": []},
                "return": {"properties": {"a": {"type": "string"}, "b": {"type": "integer"}}},
            },
        }
    }
    new = {
        "methods": {
            "kept": {
                "stability": "stable",
                "params": {"type": "object", "properties": {}, "required": []},
                "return": {
                    "properties": {"a": {"type": "string", "maxLength": 3}, "c": {}},
                    "required": ["c"],
                },
            }
        }
    }
    assert schema_changes(old, new) == [
        "gone: a stable method was removed",
        "kept returns.a: maxLength absent became 3",
        "kept returns.b was removed",
        "kept returns.c was added as a required field",
    ]


def test_assert_contract_schemas_refuses_a_provider(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="is not a contract declaration"):
        assert_contract_schemas(Library, snapshots=tmp_path)
