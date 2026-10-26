"""Owners, providers and per-provider quarantine (design §1, §6; S10)."""

from __future__ import annotations

import logging
from datetime import timedelta

import pytest
from cyclopts import App
from pydantic import BaseModel

from core_test_support.contracts.support import (
    BOOKS,
    BY_AUTHOR,
    LIBRARY_CONFIG,
    Book,
    BookSource,
    Library,
    LibrarySettings,
    ShelfSettings,
    Volume,
    compose,
    library_spec,
    shelf_spec,
    shop_spec,
    write_config,
)
from untaped.runtime import bootstrap
from untaped.runtime.contracts.registry import Quarantined, doctor_rows, every_offer, offers, reset
from untaped.runtime.registry import PluginCandidate, PluginSpec
from untaped.sdk.contracts import Contract, Failed, Ok, Provider, Skipped, gather
from untaped.sdk.contracts._declare import contract_of
from untaped.sdk.errors import ConfigError, ExitCode
from untaped.sdk.plugin import Plugin
from untaped.sdk.records import DuplicateKindError
from untaped.sdk.testing import plugin_candidate


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

    compose(shelf_spec(), PluginSpec(name="library", provides={"shelf": broken}), shop_spec())
    monkeypatch.setattr("untaped.sdk.contracts._gather.ranking", lambda *_: ("library",))
    caplog.set_level(logging.DEBUG, logger="untaped")
    library = gather(BookSource.books)()[0]
    assert isinstance(library, Failed)
    assert library.reason == "bad-provider"
    assert library.error.exit_code == ExitCode.ENVIRONMENT
    assert "of library raised RuntimeError: boom" in str(library.error)
    logged = [r for r in caplog.records if r.name == "untaped.contracts"]
    assert [r.exc_info[1] for r in logged if r.exc_info] == [error]


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


class _WrongSettings(BookSource, Provider[_OtherSettings]):
    def books(self) -> list[Book]:
        return []


@pytest.mark.parametrize(
    ("value", "reason", "detail"),
    [
        (_NoBridge(), "missing-bridge", "must fill to_book"),
        ("not a provider", "bad-provider", "which is not a provider"),
        (_ElsewhereProvider(), "bad-provider", "which shelf does not declare"),
        (_WrongSettings(), "bad-provider", "Provider[_OtherSettings]"),
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
    [row] = doctor_rows()
    assert (row.plugin, row.status, row.title) == ("gitlab", "warn", "unused-method")
    assert "search" in row.detail
    assert "_helper" not in row.detail


def _rule_like(function):  # type: ignore[no-untyped-def]
    function.__untaped_provider_helper__ = True
    return function


class _WithHelpers(BookSource):
    def books(self) -> list[Book]:
        return []

    @_rule_like
    def docs_page_length(self) -> None: ...


def test_a_method_marked_as_a_provider_helper_is_not_unused() -> None:
    """A provider base's own methods (lint's ``@rule``) are no removed contract method."""
    compose(shelf_spec(), PluginSpec(name="gitlab", provides={"shelf": lambda: (_WithHelpers(),)}))
    assert _rows() == [("gitlab", "contract-provider", "pass", "active")]


def test_doctor_has_a_row_per_provider_and_an_inactive_one_passes() -> None:
    write_config(LIBRARY_CONFIG)
    compose(shelf_spec(), library_spec(), shop_spec())
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
    compose(shelf_spec(), library_spec(_NoBridge()))
    [row] = doctor_rows()
    assert (row.status, row.title) == ("warn", "missing-bridge")
    assert row.detail.endswith("; upgrade untaped-library")
    compose()
    assert doctor_rows() == []


def test_a_skipped_answer_names_why() -> None:
    compose(shelf_spec(), library_spec(), shop_spec())
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
    compose(shelf_spec(), acme, odd, shop_spec())
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

    unlisted = PluginSpec(name="shelf", contracts=lambda: (BookSource,), caches=(BY_AUTHOR,))
    compose(unlisted, shop_spec())
    assert unreadable_owners() == ["shelf"]
    rows = {(row.plugin, row.title): row.detail for row in doctor_rows()}
    assert rows[("shelf", "bad-contracts")] == (
        "contracts couldn't be read: TypeError: BookSource.books is cached in "
        "shelf.book_source_books, which is not one of shelf's caches (list it on "
        "PluginSpec.caches)"
    )


def test_an_offer_waiting_for_its_owner_is_a_pass_in_doctor() -> None:
    compose(shop_spec())
    [row] = doctor_rows()
    assert (row.plugin, row.status, row.title) == ("shop", "pass", "owner-not-installed")
    assert row.detail == "waits for shelf (not installed)"
    compose(shelf_spec(), library_spec(), PluginSpec(name="kiosk", provides={"git": lambda: ()}))
    assert ("kiosk", "contract-provider", "pass", "owner-not-installed") in _rows()


def _versioned(owner_version: str, *requires: str) -> None:
    reset()
    bootstrap.compose_root(
        candidates=[
            PluginCandidate(
                distribution="untaped-shelf",
                name="shelf",
                target=shelf_spec(),
                distribution_version=owner_version,
            ),
            PluginCandidate(
                distribution="untaped-shop",
                name="shop",
                target=shop_spec(),
                requires_dist=requires,
            ),
        ]
    )


def test_an_owner_outside_the_providers_range_quarantines_its_offer() -> None:
    _versioned("2.0.0", "untaped-shelf>=1,<2; extra == 'shelf'")
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
        ("untaped-shelf>=9",),  # not under the shelf extra: not the owner range
        (),
    ],
    ids=["in-range", "unguarded", "undeclared"],
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


CACHES = (BOOKS, BY_AUTHOR)


class _Lending(BookSource, Provider):
    def books(self) -> list[Book]:
        return [Book(title="Dune")]


class _LendsSettings(BaseModel):
    pass


class _Lends(Plugin):
    """Lend books to an 11.x shelf."""

    settings = _LendsSettings
    fills = (_Lending,)


def test_a_plugin_class_s_fill_reaches_the_11x_owner_whose_package_defines_the_contract() -> None:
    reset()
    lends = PluginCandidate(distribution="untaped-lends", name="lends", target=_Lends)
    # the owner's settings model locates its package, which defines the contract
    shelf = PluginSpec(
        name="shelf", settings=ShelfSettings, contracts=lambda: (BookSource,), caches=CACHES
    )
    bootstrap.compose_root(candidates=[plugin_candidate(shelf), lends])
    [answer] = gather(BookSource.books)()
    assert (answer.plugin, isinstance(answer, Ok)) == ("lends", True)


def test_a_fill_no_composed_plugin_owns_waits_silently() -> None:
    reset()
    lends = PluginCandidate(distribution="untaped-lends", name="lends", target=_Lends)
    result = bootstrap.compose_root(candidates=[lends])
    [spec] = [each.spec for each in result.plugins if each.spec.name == "lends"]
    assert dict(spec.provides) == {}  # a composition row once the 11.x reader goes


def test_a_start_from_the_kept_declaration_still_routes_the_fill_to_the_11x_owner() -> None:
    from untaped.runtime import registry

    target = f"{_Lends.__module__}:{_Lends.__qualname__}"
    lends = PluginCandidate(distribution="untaped-lends", name="lends", target=target)
    shelf = PluginSpec(
        name="shelf", settings=ShelfSettings, contracts=lambda: (BookSource,), caches=CACHES
    )
    ordered = [plugin_candidate(shelf), lends]
    fresh = registry._compose(ordered)
    replayed = registry._compose(ordered, fresh.mount.declaration)
    for result in (fresh, replayed):
        [spec] = [each.spec for each in result.plugins if each.spec.name == "lends"]
        assert list(spec.provides) == ["shelf"]


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
