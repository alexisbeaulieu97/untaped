"""``gather``: who is asked, in what order, and what each answer holds (design §3, §6)."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from test_contracts.support import (
    LIBRARY_CONFIG,
    Book,
    BookSource,
    Kiosk,
    Library,
    Shop,
    compose,
    kiosk_spec,
    library_spec,
    shelf_spec,
    shop_spec,
    write_config,
)
from untaped.contracts import Failed, Ok, Skipped, Source, gather
from untaped.errors import ConfigError, ExitCode, HttpTransportError, UntapedError


@pytest.fixture
def rank(monkeypatch: pytest.MonkeyPatch) -> object:
    """Rank providers the way ``untaped rank`` will: ``rank("shop", "library")``."""

    def set_rank(*plugins: str) -> None:
        monkeypatch.setattr("untaped.contracts._gather.ranking", lambda *_: plugins)

    return set_rank


def _cache_files() -> list[Path]:
    import os

    root = Path(os.environ["HOME"]) / ".untaped" / "plugins"
    return sorted(path for path in root.rglob("*.json"))


def test_answers_come_in_rank_order_then_by_name(rank) -> None:  # type: ignore[no-untyped-def]
    compose(shelf_spec(), library_spec(), shop_spec(), kiosk_spec())
    write_config(LIBRARY_CONFIG)
    answers = gather(BookSource.books)()
    assert [answer.plugin for answer in answers] == ["kiosk", "library", "shop"]
    assert all(isinstance(answer, Ok) and answer.rank is None for answer in answers)
    rank("shop", "kiosk")
    answers = gather(BookSource.books)()
    assert [(answer.plugin, answer.rank) for answer in answers] == [
        ("shop", 0),
        ("kiosk", 1),
        ("library", None),
    ]
    assert answers.rank_command == "untaped rank shelf.book_source books shop kiosk library"


def test_a_provider_not_configured_is_skipped_and_none_ready_is_exit_4() -> None:
    compose(shelf_spec(), library_spec(), shop_spec())
    [library, shop] = gather(BookSource.books)()
    assert isinstance(library, Skipped)
    assert library.reason == "not-configured"
    assert isinstance(shop, Ok)
    compose(shelf_spec(), library_spec())
    with pytest.raises(
        ConfigError, match=r"no provider of shelf\.book_source\.books is ready"
    ) as err:
        gather(BookSource.books)()
    assert err.value.exit_code == ExitCode.ENVIRONMENT
    assert "set library.catalog" in str(err.value)


def test_only_providers_that_fill_the_needed_methods_are_asked() -> None:
    compose(shelf_spec(), library_spec(), shop_spec(), kiosk_spec())
    write_config(LIBRARY_CONFIG)
    assert [a.plugin for a in gather(BookSource.lookup)("Dune")] == ["shop"]
    needed = gather(BookSource.books, needs=(BookSource.lookup,))()
    assert [a.plugin for a in needed] == ["shop"]


def test_a_provider_that_raises_fails_its_own_answer_only() -> None:
    compose(shelf_spec(), library_spec(), shop_spec())
    write_config(LIBRARY_CONFIG)
    Shop.error = HttpTransportError("down", system="shop")
    [library, shop] = gather(BookSource.books, refresh=True)()
    assert isinstance(library, Ok)
    assert isinstance(shop, Failed)
    assert shop.error.exit_code == ExitCode.UNAVAILABLE
    Shop.error = KeyError("bug")
    shop = gather(BookSource.books, refresh=True)()[1]
    assert isinstance(shop, Failed)
    assert isinstance(shop.error, UntapedError)
    assert shop.error.system == "shop"


def test_a_listing_drops_an_invalid_row_and_keeps_the_rest() -> None:
    compose(shelf_spec(), shop_spec())
    Shop.rows = [Book(title="Dune"), Book.model_construct(title=3), "nonsense"]
    [shop] = gather(BookSource.books)()
    assert isinstance(shop, Ok)
    assert [book.title for book in shop.value] == ["Dune"]
    assert len(shop.invalid) == 2
    assert shop.invalid[0].startswith("row 2: title")


def test_a_lookup_with_an_invalid_item_fails_whole_by_rank(rank) -> None:  # type: ignore[no-untyped-def]
    compose(shelf_spec(), shop_spec())
    Shop.rows = [Book.model_construct(title="Dune", pages="many")]
    [shop] = gather(BookSource.lookup)("Dune")
    assert isinstance(shop, Skipped)
    assert shop.reason == "invalid-item"
    rank("shop")
    [shop] = gather(BookSource.lookup)("Dune")
    assert isinstance(shop, Failed)
    assert shop.reason == "invalid-item"
    assert shop.error.exit_code == ExitCode.ENVIRONMENT
    assert shop.error.hint == "upgrade untaped-shop"


def test_items_are_validated_strictly_as_the_owner_model() -> None:
    compose(shelf_spec(), shop_spec())
    Shop.rows = [Book.model_construct(title="Dune", pages="7")]
    [shop] = gather(BookSource.books)()
    assert isinstance(shop, Ok)
    assert shop.value == []
    assert "pages" in shop.invalid[0]


def test_a_forged_source_is_unbridged() -> None:
    compose(shelf_spec(), shop_spec())
    Shop.rows = [Book(title="Dune", source=Source(plugin="library", kind="library.volume"))]
    [shop] = gather(BookSource.books)()
    assert isinstance(shop, Skipped)
    assert shop.reason == "unbridged-item"
    assert "'library'" in shop.detail


def test_an_owner_model_item_gets_the_provider_source() -> None:
    compose(shelf_spec(), kiosk_spec())
    Kiosk.rows = [Book(title="Dune")]
    [kiosk] = gather(BookSource.books)()
    assert isinstance(kiosk, Ok)
    assert kiosk.value[0].source == Source(plugin="kiosk", kind="shelf.book")


def test_a_bridged_provider_returning_an_unstamped_item_is_unbridged() -> None:
    class Sloppy(Library):
        def books(self) -> list[Book]:
            return [Book(title="Dune")]

    compose(shelf_spec(), library_spec(Sloppy()))
    write_config(LIBRARY_CONFIG)
    [library] = gather(BookSource.books)()
    assert isinstance(library, Skipped)
    assert library.reason == "unbridged-item"


def test_the_answer_cache_serves_within_the_ttl_and_refresh_directs_it() -> None:
    compose(shelf_spec(), library_spec())
    write_config(LIBRARY_CONFIG)
    [first] = gather(BookSource.books)()
    [second] = gather(BookSource.books)()
    assert Library.calls == 1
    assert isinstance(first, Ok) and isinstance(second, Ok)
    assert second.value == first.value
    assert second.refreshed_at == first.refreshed_at
    gather(BookSource.books, refresh=True)()
    assert Library.calls == 2
    [cached_only] = gather(BookSource.books, refresh=False)()
    assert Library.calls == 2
    assert isinstance(cached_only, Ok)
    [path] = _cache_files()
    assert path.parts[-4:-1] == ("cache", "shelf.book_source.books", "default")
    stored = json.loads(path.read_text())
    assert stored["untaped"] == "1"
    assert [row["title"] for row in stored["value"]] == ["Dune", "Emma"]


def test_refresh_false_without_a_cached_answer_is_skipped_no_cache() -> None:
    compose(shelf_spec(), library_spec())
    write_config(LIBRARY_CONFIG)
    [library] = gather(BookSource.books, refresh=False)()
    assert isinstance(library, Skipped)
    assert library.reason == "no-cache"
    assert Library.calls == 0


def test_a_failed_live_call_serves_the_cached_answer_marked_stale() -> None:
    compose(shelf_spec(), library_spec())
    write_config(LIBRARY_CONFIG)
    [fresh] = gather(BookSource.books)()
    Library.error = HttpTransportError("HTTP 503", system="library")
    [stale] = gather(BookSource.books, refresh=True)()
    assert isinstance(fresh, Ok) and isinstance(stale, Ok)
    assert stale.value == fresh.value
    assert stale.stale is not None
    assert stale.stale.error is Library.error
    assert stale.refreshed_at == fresh.refreshed_at


def test_a_failed_live_call_with_no_cached_answer_fails() -> None:
    compose(shelf_spec(), library_spec())
    write_config(LIBRARY_CONFIG)
    Library.error = HttpTransportError("HTTP 503", system="library")
    [library] = gather(BookSource.books)()
    assert isinstance(library, Failed)


def test_s20_a_changed_model_misses_the_cache_and_nothing_errors() -> None:
    """S20: the cached answer's schema hash differs, so the next call refreshes."""
    compose(shelf_spec(), library_spec())
    write_config(LIBRARY_CONFIG)
    gather(BookSource.books)()
    [path] = _cache_files()
    stored = json.loads(path.read_text())
    stored["schema"] = "0" * 64
    path.write_text(json.dumps(stored))
    [answer] = gather(BookSource.books)()
    assert isinstance(answer, Ok)
    assert Library.calls == 2
    stored["schema"] = json.loads(path.read_text())["schema"]
    stored["value"] = [{"title": 5}]
    path.write_text(json.dumps(stored))
    [answer] = gather(BookSource.books)()
    assert isinstance(answer, Ok)
    assert Library.calls == 3


def test_an_expired_entry_is_refreshed() -> None:
    compose(shelf_spec(), library_spec())
    write_config(LIBRARY_CONFIG)
    gather(BookSource.books)()
    [path] = _cache_files()
    stored = json.loads(path.read_text())
    stored["refreshed_at"] = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    path.write_text(json.dumps(stored))
    gather(BookSource.books)()
    assert Library.calls == 2


def test_the_cache_is_per_profile() -> None:
    compose(shelf_spec(), library_spec())
    write_config(
        LIBRARY_CONFIG + "  work:\n    library: {catalog: https://w.example, volumes: []}\n"
    )
    gather(BookSource.books)()
    from untaped.profile_resolver import profile_scope

    with profile_scope("work"):
        [answer] = gather(BookSource.books)()
    assert isinstance(answer, Ok)
    assert answer.value == []
    assert Library.calls == 2
    assert {path.parent.name for path in _cache_files()} == {"default", "work"}


def test_the_deadline_bounds_a_provider_requests() -> None:
    from untaped.http import _time_left

    seen: list[float | None] = []

    class Timed(Library):
        def books(self) -> list[Book]:
            seen.append(_time_left())
            return []

    compose(shelf_spec(), library_spec(Timed()))
    write_config(LIBRARY_CONFIG)
    gather(BookSource.books, refresh=True, deadline=timedelta(seconds=30))()
    gather(BookSource.books, refresh=True)()
    assert seen[0] is not None and 0 < seen[0] <= 30
    assert seen[1] is None
    started = time.monotonic()
    assert time.monotonic() - started < 1
