"""Owners, providers and per-provider quarantine (design §1, §6; S10)."""

from __future__ import annotations

import contextlib
import io

import pytest
from pydantic import BaseModel

from core_test_support.contracts.support import (
    LIBRARY_CONFIG,
    Book,
    BookSource,
    Library,
    LibrarySettings,
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
from untaped.runtime.registry import PluginCandidate
from untaped.sdk.contracts import Errored, Ok, Provider, Skipped, gather
from untaped.sdk.contracts._declare import contract_of
from untaped.sdk.errors import ConfigError, ExitCode
from untaped.sdk.plugin import Plugin


def _rows() -> list[tuple[str, str, str, str]]:
    return [(row.plugin, row.check, row.status, row.title) for row in doctor_rows()]


def _info():  # type: ignore[no-untyped-def]
    info = contract_of(BookSource)
    assert info is not None
    return info


def _reasons() -> dict[str, str]:
    return {entry.plugin: entry.reason for entry in every_offer() if isinstance(entry, Quarantined)}


def test_a_contract_nobody_declares_is_a_config_error() -> None:
    compose(shop_plugin())
    with pytest.raises(ConfigError, match="no installed plugin declares the contract BookSource"):
        gather(BookSource.books)()


class _NoBridge(BookSource[Volume], Provider[LibrarySettings]):
    def books(self) -> list[Book]:
        return []


def test_a_quarantined_ranked_provider_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    compose(shelf_plugin(), library_plugin(_NoBridge), shop_plugin())
    monkeypatch.setattr("untaped.sdk.contracts._gather.ranking", lambda *_: ("library",))
    library = gather(BookSource.books)()[0]
    assert isinstance(library, Errored)
    assert library.reason == "missing-bridge"
    assert library.error.exit_code == ExitCode.ENVIRONMENT


def test_a_provider_breaking_a_rule_is_quarantined_alone() -> None:
    """A rule of the offer's own; the plugin class's rules are composition's (test_composition)."""
    compose(shelf_plugin(), library_plugin(_NoBridge, Library), shop_plugin())
    write_config(LIBRARY_CONFIG)
    [quarantined] = [entry for entry in every_offer() if isinstance(entry, Quarantined)]
    assert quarantined.reason == "missing-bridge"
    assert "must fill to_book" in quarantined.detail
    answers = {a.plugin: a for a in gather(BookSource.books)()}
    assert isinstance(answers["shop"], Ok)


class _Again(Library):
    """A second provider of the contract ``Library`` fills."""


def test_one_plugin_providing_a_contract_twice_is_quarantined() -> None:
    compose(shelf_plugin(), library_plugin(Library, _Again))
    write_config(LIBRARY_CONFIG)
    assert [entry.reason for entry in offers(_info())] == ["bad-provider", "bad-provider"]  # type: ignore[union-attr]


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


def test_help_and_plugin_list_never_build_a_provider() -> None:
    made: list[int] = []

    class Counted(Library):
        def __init__(self) -> None:
            made.append(1)

    found = [shelf_plugin(), library_plugin(Counted)]
    for argv in (["--help"], ["plugin", "list"]):
        app = bootstrap.build_root_app(candidates=found)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.suppress(SystemExit):
            app.meta(argv)
    assert made == []


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


def test_an_owner_outside_the_providers_range_quarantines_its_offer() -> None:
    _versioned("2.0.0", "untaped-shelf>=1,<2")
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
        ("untaped-shelf>=1,<2",),  # a plain dependency, as a provider declares it
        ("untaped-shelf>=9; extra == 'shelf'",),  # under an extra: not the owner range
        (),
    ],
    ids=["in-range", "extra", "undeclared"],
)
def test_an_owner_in_range_or_without_a_declared_range_is_asked(requires: tuple[str, ...]) -> None:
    _versioned("1.4.0", *requires)
    [entry] = offers(_info())
    assert not isinstance(entry, Quarantined)


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
    [spec] = [each for each in result.plugins if each.name == "lends"]
    assert spec.fill_owners == ()
