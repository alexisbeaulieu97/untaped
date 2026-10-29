"""Owners, providers and per-provider quarantine (design §1, §6; S10)."""

from __future__ import annotations

import logging
from datetime import timedelta

import pytest
from cyclopts import App
from pydantic import BaseModel

from core_test_support.contracts.support import (
    LIBRARY_CONFIG,
    Book,
    BookSource,
    Library,
    LibrarySettings,
    Shop,
    Volume,
    compose,
    library_plugin,
    plugin,
    shelf_plugin,
    shop_plugin,
    write_config,
)
from untaped.runtime import bootstrap
from untaped.runtime.contracts.registry import Quarantined, doctor_rows, every_offer, offers, reset
from untaped.runtime.registry import PluginCandidate, PluginSpec
from untaped.sdk.contracts import Contract, Errored, Ok, Provider, Skipped, gather
from untaped.sdk.contracts._declare import contract_of
from untaped.sdk.errors import ConfigError, ExitCode
from untaped.sdk.plugin import Plugin
from untaped.sdk.records import DuplicateKindError, ForeignKindError, Record


def _rows() -> list[tuple[str, str, str, str]]:
    return [(row.plugin, row.check, row.status, row.title) for row in doctor_rows()]


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
    compose(shop_plugin())
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
    composed = compose(shelf_plugin(), library, dup, shop_plugin())
    assert {plugin.spec.name for plugin in composed.plugins} == {
        "untaped",
        "shelf",
        "library",
        "dup",
        "shop",
    }
    assert composed.quarantine == ()
    answers = gather(BookSource.books)()
    assert [(a.plugin, type(a).__name__) for a in answers] == [
        ("dup", "Skipped"),
        ("library", "Skipped"),
        ("shop", "Ok"),
    ]
    assert _reasons() == {"dup": "duplicate-kind", "library": "bad-provider"}


def test_a_quarantined_ranked_provider_fails(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    error = RuntimeError("boom")

    def broken() -> tuple[Contract, ...]:
        raise error

    compose(shelf_plugin(), PluginSpec(name="library", provides={"shelf": broken}), shop_plugin())
    monkeypatch.setattr("untaped.sdk.contracts._gather.ranking", lambda *_: ("library",))
    caplog.set_level(logging.DEBUG, logger="untaped")
    library = gather(BookSource.books)()[0]
    assert isinstance(library, Errored)
    assert library.reason == "bad-provider"
    assert library.error.exit_code == ExitCode.ENVIRONMENT
    assert "of library raised RuntimeError: boom" in str(library.error)
    logged = [r for r in caplog.records if r.name == "untaped.contracts"]
    assert [r.exc_info[1] for r in logged if r.exc_info] == [error]


class _NoBridge(BookSource[Volume], Provider[LibrarySettings]):
    def books(self) -> list[Book]:
        return []


def test_a_provider_breaking_a_rule_is_quarantined_alone() -> None:
    """A rule of the offer's own; the plugin class's rules are composition's (test_composition)."""
    compose(shelf_plugin(), library_plugin(_NoBridge, Library), shop_plugin())
    write_config(LIBRARY_CONFIG)
    [quarantined] = [entry for entry in every_offer() if isinstance(entry, Quarantined)]
    assert quarantined.reason == "missing-bridge"
    assert "must fill to_book" in quarantined.detail
    answers = {a.plugin: a for a in gather(BookSource.books)()}
    assert isinstance(answers["shop"], Ok)


class _Kindless(Record):
    name: str


class _NoKind(BookSource[_Kindless]):
    def to_book(self, item: _Kindless) -> Book:
        return Book(title=item.name)

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


class _WrongSettings(BookSource, Provider[_OtherSettings]):
    def books(self) -> list[Book]:
        return []


@pytest.mark.parametrize(
    ("value", "reason", "detail"),
    [
        (_NoBridge(), "missing-bridge", "must fill to_book"),
        (_NoKind(), "unresolved-item-type", "_Kindless has no kind"),
        ("not a provider", "bad-provider", "which is not a provider"),
        (_ElsewhereProvider(), "bad-provider", "which shelf does not declare"),
        (_WrongSettings(), "bad-provider", "Provider[_OtherSettings]"),
    ],
)
def test_an_11x_offer_breaking_a_rule_is_quarantined_alone(
    value: object, reason: str, detail: str
) -> None:
    """The 11.x offer's rules; a plugin class can break only the bridge one at the offer."""
    offered = (value, Library())
    library = PluginSpec(
        name="library", settings=LibrarySettings, provides={"shelf": lambda: offered}
    )
    compose(shelf_plugin(), library, shop_plugin())
    write_config(LIBRARY_CONFIG)
    [quarantined] = [entry for entry in every_offer() if isinstance(entry, Quarantined)]
    assert quarantined.reason == reason
    assert detail in quarantined.detail
    answers = {a.plugin: a for a in gather(BookSource.books)()}
    assert isinstance(answers["shop"], Ok)


class _Again(Library):
    """A second provider of the contract ``Library`` fills."""


def test_one_plugin_providing_a_contract_twice_is_quarantined() -> None:
    compose(shelf_plugin(), library_plugin(Library, _Again))
    write_config(LIBRARY_CONFIG)
    assert [entry.reason for entry in offers(_info())] == ["bad-provider", "bad-provider"]  # type: ignore[union-attr]


def _waiting_shop() -> PluginSpec:
    """An 11.x offer to ``shelf``: a plugin class's fill with no composed owner waits silently."""
    return PluginSpec(name="shop", provides={"shelf": lambda: (Shop(),)})


def test_an_offer_to_an_owner_not_installed_is_reported() -> None:
    compose(_waiting_shop())
    assert _reasons() == {"shop": "owner-not-installed"}


def test_the_providers_group_loads_once_per_composition() -> None:
    made: list[int] = []

    class Counted(Library):
        def __init__(self) -> None:
            made.append(1)

    spec = library_plugin(Counted)
    compose(shelf_plugin(), spec)
    write_config(LIBRARY_CONFIG)
    gather(BookSource.books)()
    gather(BookSource.books)()
    assert made == [1]
    compose(shelf_plugin(), spec)
    gather(BookSource.books)()
    assert len(made) == 2


class _Outdated(BookSource, Provider):
    """Fills a method a later shelf removed (S10)."""

    def books(self) -> list[Book]:
        return [Book(title="Dune")]

    def search(self, text: str) -> list[Book]:
        return []

    def _helper(self) -> None:
        pass


def test_s10_a_removed_method_is_never_called_and_doctor_notes_it() -> None:
    """S10: no quarantine, doctor ! unused-method, everything else works."""
    outdated = plugin("gitlab", fills=(_Outdated,))
    compose(shelf_plugin(), outdated)
    [answer] = gather(BookSource.books)()
    assert isinstance(answer, Ok)
    assert _reasons() == {}
    [row] = doctor_rows()
    assert (row.plugin, row.status, row.title) == ("gitlab", "warn", "unused-method")
    assert "search" in row.detail
    assert "_helper" not in row.detail


def _rule_like(function):  # type: ignore[no-untyped-def]
    function.__untaped_provider_helper__ = True
    return function


class _WithHelpers(BookSource, Provider):
    def books(self) -> list[Book]:
        return []

    @_rule_like
    def docs_page_length(self) -> None: ...


def test_a_method_marked_as_a_provider_helper_is_not_unused() -> None:
    """A provider base's own methods (lint's ``@rule``) are no removed contract method."""
    compose(shelf_plugin(), plugin("gitlab", fills=(_WithHelpers,)))
    assert _rows() == [("gitlab", "contract-provider", "pass", "active")]


def test_doctor_has_a_row_per_provider_and_an_inactive_one_passes() -> None:
    write_config(LIBRARY_CONFIG)
    compose(shelf_plugin(), library_plugin(), shop_plugin())
    assert _rows() == [
        ("library", "contract-provider", "pass", "active"),
        ("shop", "contract-provider", "pass", "active"),
    ]
    assert doctor_rows()[1].detail == (
        "fills shelf.book_source: books, lookup, by_author, first, count"
    )
    write_config("")
    [library, _] = doctor_rows()
    assert (library.status, library.title) == ("pass", "not-configured")
    assert library.detail == "shelf.book_source: waits for library.catalog"
    compose(shelf_plugin(), library_plugin(_NoBridge))
    [row] = doctor_rows()
    assert (row.status, row.title) == ("warn", "missing-bridge")
    assert row.detail.endswith("; upgrade untaped-library")
    compose()
    assert doctor_rows() == []


def test_a_skipped_answer_names_why() -> None:
    compose(shelf_plugin(), library_plugin(), shop_plugin())
    library = gather(BookSource.books)()[0]
    assert isinstance(library, Skipped)
    assert "catalog" in library.detail


def test_an_owner_whose_contracts_break_takes_only_its_own_contracts_down(
    caplog: pytest.LogCaptureFixture,
) -> None:
    error = ImportError("acme api broke")

    def broken() -> tuple[type[Contract], ...]:
        raise error

    acme = PluginSpec(name="acme", contracts=broken)
    odd = PluginSpec(name="odd", contracts=lambda: (Book,))  # type: ignore[arg-type, return-value]
    compose(shelf_plugin(), acme, odd, shop_plugin())
    caplog.set_level(logging.DEBUG, logger="untaped")
    [shop] = gather(BookSource.books)()
    assert isinstance(shop, Ok)
    rows = {(row.plugin, row.title): row.detail for row in doctor_rows()}
    assert rows[("acme", "bad-contracts")] == (
        "contracts couldn't be read: ImportError: acme api broke"
    )
    logged = [r for r in caplog.records if r.name == "untaped.contracts" and r.exc_info]
    assert error in [r.exc_info[1] for r in logged if r.exc_info]
    assert ("odd", "bad-contracts") in rows

    class Gadgets(Contract):
        def gadgets(self) -> list[Book]:
            raise NotImplementedError

    with pytest.raises(ConfigError, match="acme's contracts couldn't be read"):
        gather(Gadgets.gadgets)()


def test_an_owner_that_does_not_list_a_cached_methods_cache_is_unreadable() -> None:
    from untaped.runtime.contracts.registry import unreadable_owners
    from untaped.sdk.contracts import cached
    from untaped.sdk.storage.cache import Cache

    class Listed(Contract):
        @cached(Cache("shelf", "listed_rows", label="rows", max_age=timedelta(hours=1)))
        def rows(self) -> list[Book]:
            raise NotImplementedError

    compose(PluginSpec(name="shelf", contracts=lambda: (Listed,)))
    assert unreadable_owners() == ["shelf"]
    rows = {(row.plugin, row.title): row.detail for row in doctor_rows()}
    assert rows[("shelf", "bad-contracts")] == (
        "contracts couldn't be read: TypeError: test_an_owner_that_does_not_list_a_cached_"
        "methods_cache_is_unreadable.<locals>.Listed.rows is cached in "
        "shelf.listed_rows, which is not one of shelf's caches (list it on "
        "PluginSpec.caches)"
    )


def test_an_offer_waiting_for_its_owner_is_a_pass_in_doctor() -> None:
    compose(_waiting_shop())
    [row] = doctor_rows()
    assert (row.plugin, row.status, row.title) == ("shop", "pass", "owner-not-installed")
    assert row.detail == "waits for shelf (not installed)"
    elsewhere = PluginSpec(name="kiosk", provides={"git": lambda: ()})
    compose(shelf_plugin(), library_plugin(), elsewhere)
    assert ("kiosk", "contract-provider", "pass", "owner-not-installed") in _rows()


def _versioned(owner_version: str, *requires: str) -> None:
    reset()
    bootstrap.compose_root(
        candidates=[
            PluginCandidate(
                distribution="untaped-shelf",
                name="shelf",
                target=shelf_plugin().target,
                distribution_version=owner_version,
            ),
            PluginCandidate(
                distribution="untaped-shop",
                name="shop",
                target=shop_plugin().target,
                requires_dist=requires,
            ),
        ]
    )


@pytest.mark.parametrize(
    "requires",
    ["untaped-shelf>=1,<2; extra == 'shelf'", "untaped-shelf>=1,<2"],
    ids=["extra", "plain"],
)
def test_an_owner_outside_the_providers_range_quarantines_its_offer(requires: str) -> None:
    _versioned("2.0.0", requires)
    [entry] = offers(_info())
    assert isinstance(entry, Quarantined)
    assert entry.reason == "owner-out-of-range"
    assert entry.detail == "shop requires untaped-shelf>=1,<2 for shelf, but 2.0.0 is installed"
    [row] = [row for row in doctor_rows() if row.plugin == "shop"]
    assert (row.status, row.title) == ("warn", "owner-out-of-range")
    assert row.detail.endswith("upgrade untaped-shop")


@pytest.mark.parametrize(
    "requires",
    [
        ("untaped-shelf>=1,<2; extra == 'shelf'",),
        ("untaped-shelf>=1,<2",),  # a plain dependency, as a 12.0 provider declares it
        ("untaped-shelf>=9; extra == 'other'",),  # under another extra: not the owner range
        (),
    ],
    ids=["in-range", "plain", "other-extra", "undeclared"],
)
def test_an_owner_in_range_or_without_a_declared_range_is_asked(requires: tuple[str, ...]) -> None:
    _versioned("1.4.0", *requires)
    [entry] = offers(_info())
    assert not isinstance(entry, Quarantined)


def test_an_owner_extra_behind_a_marker_the_environment_cannot_fill_adds_no_requirement() -> None:
    from untaped.runtime.registry import owner_requirement

    lines = [
        'untaped-shelf>=1; extra == "shelf" and "x" in extras',
        'untaped-shelf>=2; extra == "shelf"',
    ]
    found = owner_requirement(lines, "shelf")
    assert found is not None
    assert str(found.specifier) == ">=2"


class _Lending(BookSource, Provider):
    def books(self) -> list[Book]:
        return [Book(title="Dune")]


class _LendsSettings(BaseModel):
    pass


class _Lends(Plugin):
    """Lend books to a shelf."""

    settings = _LendsSettings
    fills = (_Lending,)


def test_a_fill_no_composed_plugin_owns_waits_silently() -> None:
    reset()
    lends = PluginCandidate(distribution="untaped-lends", name="lends", target=_Lends)
    result = bootstrap.compose_root(candidates=[lends])
    [spec] = [each.spec for each in result.plugins if each.spec.name == "lends"]
    assert dict(spec.provides) == {}


def test_an_11x_owner_s_cache_with_no_plugin_and_name_is_unreadable() -> None:
    from untaped.runtime.contracts.registry import unreadable_owners
    from untaped.sdk.contracts import cached
    from untaped.sdk.storage.cache import Cache

    class Unnamed(Contract):
        @cached(Cache(label="rows", max_age=timedelta(hours=1)))
        def rows(self) -> list[Book]:
            raise NotImplementedError

    compose(PluginSpec(name="rows", contracts=lambda: (Unnamed,)))
    assert unreadable_owners() == ["rows"]
    rows = {(row.plugin, row.title): row.detail for row in doctor_rows()}
    assert rows[("rows", "bad-contracts")].endswith(
        "Unnamed.rows is cached in a cache with no plugin and name: spell it "
        "Cache('rows', 'unnamed_rows', ...) and list it on PluginSpec.caches"
    )


def test_an_11x_provider_whose_kind_is_foreign_is_a_bad_provider() -> None:
    def foreign() -> tuple[Contract, ...]:
        raise ForeignKindError("kind 'shelf.loan' is declared under library's package")

    compose(shelf_plugin(), PluginSpec(name="library", provides={"shelf": foreign}))
    assert _reasons() == {"library": "bad-provider"}
