"""``LibraryBooks``, the library's provider of shelf's ``BookSource``, asked through shelf."""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest
from untaped_library.plugin import Library
from untaped_library.providers.shelf import LibraryBooks
from untaped_library.records import Volume
from untaped_library.settings import LibrarySettings, VolumeEntry

from untaped.sdk import StaleData, home
from untaped.sdk.testing import Install, ManualClock, assert_fills

Installer = Callable[..., Install]

_VOLUMES = LibrarySettings(
    volumes=[
        VolumeEntry(shelf_mark="A1", name="Dune", pages=412),
        VolumeEntry(shelf_mark="B2", name="Emma", pages=474),
    ]
)


def test_the_library_fills_the_shelf_contract() -> None:
    assert_fills(
        LibraryBooks,
        samples=[
            Volume(shelf_mark="A1", name="Dune", pages=412),
            {"shelf_mark": "B2", "name": "Emma"},
        ],
    )


def test_unconfigured_the_owner_has_no_ready_provider(install: Installer) -> None:
    ran = install(Library).cli("shelf find Dune")
    assert ran.exit == 4
    assert any(
        "library: no volumes configured (set library.volumes)" in line for line in ran.stderr
    )


def test_the_owner_lists_the_library_books_with_their_source(install: Installer) -> None:
    ran = install(Library, settings=_VOLUMES).cli("shelf list --format json")
    assert ran.exit == 0, ran.stderr
    rows = json.loads(ran.stdout)
    assert [row["title"] for row in rows] == ["Dune", "Emma"]
    assert rows[0]["source"] == {
        "plugin": "library",
        "kind": "library.volume",
        "record": {"shelf_mark": "A1", "name": "Dune", "pages": 412},
    }


def test_the_owner_finds_one_book_and_reports_none(install: Installer) -> None:
    library = install(Library, settings=_VOLUMES)
    found = library.cli("shelf find Emma --format json")
    assert found.exit == 0, found.stderr
    assert json.loads(found.stdout)["pages"] == 474
    missing = library.cli("shelf find Ulysses")
    assert missing.exit == 2
    assert any("no provider of shelf.book_source matched" in line for line in missing.stderr)


def test_the_answer_is_cached_per_provider_settings(install: Installer) -> None:
    library = install(Library, settings=_VOLUMES)
    path = home().expanduser() / "config.yml"
    path.write_text(
        path.read_text(encoding="utf-8")
        + "profiles:\n"
        + "  same:\n"
        + "    library:\n"
        + "      volumes:\n"
        + "        - {shelf_mark: A1, name: Dune, pages: 412}\n"
        + "        - {shelf_mark: B2, name: Emma, pages: 474}\n"
        + "  other:\n"
        + "    library:\n"
        + "      volumes: [{shelf_mark: C3, name: Kim, pages: 9}]\n",
        encoding="utf-8",
    )
    cached = home().expanduser() / "plugins" / "shelf" / "cached"
    assert library.cli("shelf list").exit == 0
    assert library.cli("--profile same shelf list").exit == 0
    [entry] = cached.rglob("*.json")  # the same library settings share the answer
    assert entry.parent.parent.name == "book_source_books"
    assert library.cli("--profile other shelf list").exit == 0
    assert len(list(cached.rglob("*.json"))) == 2


def test_a_failed_refresh_serves_the_cached_books_with_one_stale_warning(
    install: Installer, clock: ManualClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = install(Library, settings=_VOLUMES)
    assert library.cli("shelf list").exit == 0
    clock.advance(hours=2)  # past the max age: the next call is live

    def down(*_args: object, **_kwargs: object) -> Volume:
        raise RuntimeError("the catalogue is down")

    monkeypatch.setattr(Volume, "model_validate", down)
    ran = library.cli("shelf list --format json")

    assert ran.exit == 0, ran.stderr
    assert [row["title"] for row in json.loads(ran.stdout)] == ["Dune", "Emma"]
    [stale] = ran.diagnostics
    assert isinstance(stale, StaleData)
    assert (stale.plugin, stale.cache) == ("library", "shelf.book_source_books")
    assert stale.message.startswith("library: refresh failed (the catalogue is down)")


def _volume_line(**record: object) -> str:
    return json.dumps({"untaped": "1", "kind": "library.volume", "record": record}) + "\n"


def test_a_piped_volume_reaches_shelf_as_a_book_through_the_library(install: Installer) -> None:
    piped = _volume_line(shelf_mark="C3", name="Ulysses", pages=730)
    ran = install(Library, settings=_VOLUMES).cli("shelf show --stdin -f json", input=piped)
    assert ran.exit == 0, ran.stderr
    [book] = json.loads(ran.stdout)
    assert (book["title"], book["source"]["plugin"], book["source"]["kind"]) == (
        "Ulysses",
        "library",
        "library.volume",
    )
    assert book["source"]["record"]["shelf_mark"] == "C3"


def test_a_piped_volume_with_the_library_unready_exits_4(install: Installer) -> None:
    piped = _volume_line(shelf_mark="C3", name="Ulysses")
    ran = install(Library).cli("shelf show --stdin", input=piped)
    assert ran.exit == 4
    assert any("set library.volumes" in line for line in ran.stderr), ran.stderr
