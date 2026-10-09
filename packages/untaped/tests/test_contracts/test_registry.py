"""Owners, providers and per-provider quarantine (design §1, §6; S10)."""

from __future__ import annotations

import json

import pytest
from cyclopts import App
from pydantic import BaseModel

from test_contracts.support import (
    LIBRARY_CONFIG,
    Book,
    BookSource,
    Library,
    LibrarySettings,
    Volume,
    compose,
    library_spec,
    shelf_spec,
    shop_spec,
    write_config,
)
from untaped.contracts import Configured, Contract, Failed, Ok, Skipped, gather
from untaped.contracts._declare import contract_of
from untaped.contracts._registry import Quarantined, doctor_row, every_offer, offers
from untaped.errors import ConfigError, ExitCode
from untaped.plugins.registry import PluginContext, PluginSpec
from untaped.records import DuplicateKindError
from untaped.testing import invoke_root


def _info():  # type: ignore[no-untyped-def]
    info = contract_of(BookSource)
    assert info is not None
    return info


def _reasons() -> dict[str, str]:
    return {entry.plugin: entry.reason for entry in every_offer() if isinstance(entry, Quarantined)}


def test_provides_and_contracts_are_validated_on_the_spec() -> None:
    with pytest.raises(ConfigError, match="plugin name"):
        PluginSpec(name="x", provides={"Bad Owner": lambda: ()})
    with pytest.raises(ConfigError, match=r"provides\['shelf'\] must be a function"):
        PluginSpec(name="x", provides={"shelf": ()})  # type: ignore[dict-item]  # the point
    with pytest.raises(ConfigError, match="contracts must be a function"):
        PluginSpec(name="x", contracts=(BookSource,))  # type: ignore[arg-type]  # the point
    assert dict(PluginSpec(name="x").provides) == {}
    hash(PluginSpec(name="x", provides={"shelf": lambda: ()}))


def test_a_contract_nobody_declares_is_a_config_error() -> None:
    compose(shop_spec())
    with pytest.raises(ConfigError, match="no installed plugin declares the contract BookSource"):
        gather(BookSource.books)()


def test_a_broken_provides_function_quarantines_that_offer_only() -> None:
    def broken() -> tuple[Contract, ...]:
        raise RuntimeError("boom")

    def duplicate() -> tuple[Contract, ...]:
        raise DuplicateKindError("kind 'shelf.book' is declared twice")

    def build() -> App:
        return App(name="library")

    library = PluginSpec(
        name="library",
        app_factory=build,
        settings=LibrarySettings,
        provides={"shelf": broken},
    )
    dup = PluginSpec(name="dup", provides={"shelf": duplicate})
    composed = compose(shelf_spec(), library, dup, shop_spec())
    assert {plugin.spec.name for plugin in composed.plugins} == {"shelf", "library", "dup", "shop"}
    assert composed.quarantine == ()
    answers = gather(BookSource.books)()
    assert [(a.plugin, type(a).__name__) for a in answers] == [
        ("dup", "Skipped"),
        ("library", "Skipped"),
        ("shop", "Ok"),
    ]
    assert _reasons() == {"dup": "duplicate-kind", "library": "bad-provider"}


def test_a_quarantined_ranked_provider_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> tuple[Contract, ...]:
        raise RuntimeError("boom")

    compose(shelf_spec(), PluginSpec(name="library", provides={"shelf": broken}), shop_spec())
    monkeypatch.setattr("untaped.contracts._gather.ranking", lambda *_: ("library",))
    library = gather(BookSource.books)()[0]
    assert isinstance(library, Failed)
    assert library.reason == "bad-provider"
    assert library.error.exit_code == ExitCode.ENVIRONMENT


class _NoBridge(BookSource[Volume]):
    def books(self) -> list[Book]:
        return []


class _Elsewhere(Contract):
    def names(self) -> list[str]:
        raise NotImplementedError


class _ElsewhereProvider(_Elsewhere):
    def names(self) -> list[str]:
        return []


class _OtherSettings(BaseModel):
    catalog: str = ""


class _WrongSettings(BookSource, Configured[_OtherSettings]):
    def books(self) -> list[Book]:
        return []


@pytest.mark.parametrize(
    ("value", "reason", "detail"),
    [
        (_NoBridge(), "missing-bridge", "must fill to_book"),
        ("not a provider", "bad-provider", "which is not a provider"),
        (_ElsewhereProvider(), "bad-provider", "which shelf does not declare"),
        (_WrongSettings(), "bad-provider", "Configured[_OtherSettings]"),
    ],
)
def test_a_provider_breaking_a_rule_is_quarantined_alone(
    value: object, reason: str, detail: str
) -> None:
    compose(shelf_spec(), library_spec(value, Library()), shop_spec())  # type: ignore[arg-type]
    write_config(LIBRARY_CONFIG)
    [quarantined] = [entry for entry in every_offer() if isinstance(entry, Quarantined)]
    assert quarantined.reason == reason
    assert detail in quarantined.detail
    answers = {a.plugin: a for a in gather(BookSource.books)()}
    assert isinstance(answers["shop"], Ok)


def test_one_plugin_providing_a_contract_twice_is_quarantined() -> None:
    compose(shelf_spec(), library_spec(Library(), Library()))
    write_config(LIBRARY_CONFIG)
    assert [entry.reason for entry in offers(_info())] == ["bad-provider", "bad-provider"]  # type: ignore[union-attr]


def test_an_offer_to_an_owner_not_installed_is_reported() -> None:
    compose(shop_spec())
    assert _reasons() == {"shop": "owner-not-installed"}


def test_the_providers_group_loads_once_per_composition() -> None:
    made: list[int] = []

    def providers() -> tuple[Contract, ...]:
        made.append(1)
        return (Library(),)

    spec = PluginSpec(name="library", settings=LibrarySettings, provides={"shelf": providers})
    compose(shelf_spec(), spec)
    write_config(LIBRARY_CONFIG)
    gather(BookSource.books)()
    gather(BookSource.books)()
    assert made == [1]
    compose(shelf_spec(), spec)
    gather(BookSource.books)()
    assert len(made) == 2


class _Outdated(BookSource):
    """Fills a method a later shelf removed (S10)."""

    def books(self) -> list[Book]:
        return [Book(title="Dune")]

    def search(self, text: str) -> list[Book]:
        return []

    def _helper(self) -> None:
        pass


def test_s10_a_removed_method_is_never_called_and_doctor_notes_it() -> None:
    """S10: no quarantine, doctor ! unused-method, everything else works."""
    outdated = PluginSpec(name="gitlab", provides={"shelf": lambda: (_Outdated(),)})
    compose(shelf_spec(), outdated)
    [answer] = gather(BookSource.books)()
    assert isinstance(answer, Ok)
    assert _reasons() == {}
    row = doctor_row(PluginContext(settings=None))
    assert row.ok and row.warn
    assert "unused-method: search" in row.detail
    assert "_helper" not in row.detail


def test_the_doctor_row_passes_with_usable_providers_and_warns_on_quarantine() -> None:
    compose(shelf_spec(), shop_spec())
    row = doctor_row(PluginContext(settings=None))
    assert (row.ok, row.warn, row.detail) == (True, False, "1 provider(s), all usable")
    compose(shelf_spec(), library_spec(_NoBridge()))
    row = doctor_row(PluginContext(settings=None))
    assert row.warn
    assert "missing-bridge" in row.detail
    compose()
    assert doctor_row(PluginContext(settings=None)).detail == "no plugin fills a contract"


def test_doctor_shows_the_contract_providers_row() -> None:
    result = invoke_root(["doctor", "-f", "json"])
    [row] = [row for row in json.loads(result.stdout) if row["check"] == "contract-providers"]
    assert row["status"] == "pass"


def test_a_skipped_answer_names_why() -> None:
    compose(shelf_spec(), library_spec(), shop_spec())
    library = gather(BookSource.books)()[0]
    assert isinstance(library, Skipped)
    assert "catalog" in library.detail


def test_an_owner_whose_contracts_break_takes_only_its_own_contracts_down() -> None:
    def broken() -> tuple[type[Contract], ...]:
        raise ImportError("acme api broke")

    acme = PluginSpec(name="acme", contracts=broken)
    odd = PluginSpec(name="odd", contracts=lambda: (Book,))  # type: ignore[arg-type, return-value]
    compose(shelf_spec(), acme, odd, shop_spec())
    [shop] = gather(BookSource.books)()
    assert isinstance(shop, Ok)
    row = doctor_row(PluginContext(settings=None))
    assert row.ok and row.warn
    assert "acme's contracts couldn't be read: acme api broke" in row.detail
    assert "odd's contracts couldn't be read" in row.detail

    class Gadgets(Contract):
        def gadgets(self) -> list[Book]:
            raise NotImplementedError

    with pytest.raises(ConfigError, match="acme's contracts couldn't be read"):
        gather(Gadgets.gadgets)()


def test_an_offer_waiting_for_its_owner_is_a_pass_in_doctor() -> None:
    compose(shop_spec())
    row = doctor_row(PluginContext(settings=None))
    assert (row.ok, row.warn) == (True, False)
    assert row.detail == "offers wait for shelf (not installed)"
    compose(shelf_spec(), library_spec(), PluginSpec(name="kiosk", provides={"git": lambda: ()}))
    assert doctor_row(PluginContext(settings=None)).detail == (
        "1 provider(s), all usable; offers wait for git (not installed)"
    )
    compose(
        shelf_spec(), library_spec(_NoBridge()), PluginSpec(name="kiosk", provides={"git": dict})
    )
    row = doctor_row(PluginContext(settings=None))
    assert row.warn
    assert row.detail.endswith("; offers wait for git (not installed)")
