"""``gather``: who is asked, in what order, and what each answer holds (design §3, §6)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, Field, SecretStr

from core_test_support.contracts.support import (
    LIBRARY_CONFIG,
    Book,
    BookSource,
    Kiosk,
    Library,
    Shop,
    Volume,
    compose,
    kiosk_spec,
    library_spec,
    shelf_spec,
    shop_spec,
    write_config,
)
from untaped.runtime import bootstrap
from untaped.runtime.registry import PluginSpec
from untaped.runtime.run import child_run, invocation
from untaped.runtime.sink import exit_on_counted_failure
from untaped.sdk._run import recording
from untaped.sdk.contracts import Contract, Failed, NoProviderReady, Ok, Skipped, Source, gather
from untaped.sdk.diagnostic import StaleData
from untaped.sdk.errors import ConfigError, ExitCode, HttpTransportError, UntapedError
from untaped.sdk.storage._digest import settings_digest


@pytest.fixture
def rank(monkeypatch: pytest.MonkeyPatch) -> object:
    """Rank providers the way ``untaped plugin rank`` will: ``rank("shop", "library")``."""

    def set_rank(*plugins: str) -> None:
        monkeypatch.setattr("untaped.sdk.contracts._gather.ranking", lambda *_: plugins)

    return set_rank


def _cache_files() -> list[Path]:
    """Every stored answer: one file per entry, its freshness then its value."""
    import os

    root = Path(os.environ["HOME"]) / ".untaped" / "plugins"
    return sorted(root.rglob("*.v*.json"))


def _stamp(entry: Path) -> dict[str, Any]:
    """The entry's first line: what the cache knows about it."""
    stamp: dict[str, Any] = json.loads(entry.read_text().splitlines()[0])["untaped"]
    return stamp


def _value(entry: Path) -> Any:
    return json.loads(entry.read_text().splitlines()[1])


def _rewrite(entry: Path, *, stamp: dict[str, Any] | None = None, value: Any = None) -> None:
    lines = entry.read_text().splitlines()
    if stamp is not None:
        lines[0] = json.dumps({"untaped": stamp})
    if value is not None:
        lines[1] = json.dumps(value)
    entry.write_text("\n".join(lines) + "\n")


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
    assert (
        answers.rank_command() == "untaped plugin rank shelf.book_source books shop kiosk library"
    )


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
    assert shop.reason == "raised"
    Shop.error = KeyError("bug")
    shop = gather(BookSource.books, refresh=True)()[1]
    assert isinstance(shop, Failed)
    assert isinstance(shop.error, UntapedError)
    assert shop.error.system == "shop"
    assert shop.reason == "raised"


def test_none_ready_names_the_quarantined_and_the_unconfigured_apart() -> None:
    """An owner tells "nobody fills it" from "every provider is broken" without the message."""

    def broken() -> tuple[Contract, ...]:
        raise RuntimeError("boom")

    compose(shelf_spec(), library_spec(), PluginSpec(name="shop", provides={"shelf": broken}))
    with pytest.raises(NoProviderReady) as err:
        gather(BookSource.books)()
    assert err.value.quarantined == {"shop": "bad-provider"}
    assert set(err.value.not_ready) == {"library"}
    compose(shelf_spec())
    with pytest.raises(NoProviderReady) as none:
        gather(BookSource.books)()
    assert (none.value.quarantined, none.value.not_ready) == ({}, {})


def test_a_listing_drops_an_invalid_row_and_keeps_the_rest() -> None:
    compose(shelf_spec(), shop_spec())
    Shop.rows = [Book(title="Dune"), Book.model_construct(title=3), "nonsense"]
    [shop] = gather(BookSource.books)()
    assert isinstance(shop, Ok)
    assert [book.title for book in shop.value] == ["Dune"]
    assert len(shop.invalid) == 2
    assert shop.invalid[0].startswith("row 2: title")


def test_an_invalid_answer_that_is_no_listing_fails_whole_by_rank(rank) -> None:  # type: ignore[no-untyped-def]
    compose(shelf_spec(), shop_spec())
    Shop.rows = [Book.model_construct(title="Dune", pages="many")]
    [shop] = gather(BookSource.first)()
    assert isinstance(shop, Skipped)
    assert shop.reason == "invalid-item"
    rank("shop")
    [shop] = gather(BookSource.first)()
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


def test_the_answer_cache_serves_within_its_max_age_and_refresh_directs_it() -> None:
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
    assert path.parts[-6:-1] == (
        "plugins",
        "shelf",
        "cached",
        "book_source_books",
        settings_digest("shelf"),
    )
    assert [row["title"] for row in _value(path)] == ["Dune", "Emma"]
    stamp = _stamp(path)
    assert stamp["file"] == 1
    assert stamp["key"].startswith("library:")
    assert stamp["refreshed_at"] is not None


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
    assert stale.stale.reason == "raised"
    assert stale.refreshed_at == fresh.refreshed_at


def test_a_provider_bug_keeps_its_traceback_in_the_debug_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    compose(shelf_spec(), library_spec(), shop_spec())
    write_config(LIBRARY_CONFIG)
    caplog.set_level(logging.DEBUG, logger="untaped")
    Shop.error = KeyError("bug")
    shop = gather(BookSource.books, refresh=True)()[1]
    assert isinstance(shop, Failed)
    [record] = [r for r in caplog.records if r.name == "untaped.contracts"]
    assert "shop" in record.getMessage()
    assert record.exc_info is not None and record.exc_info[1] is Shop.error


def test_an_untaped_failure_logs_no_traceback(caplog: pytest.LogCaptureFixture) -> None:
    compose(shelf_spec(), library_spec(), shop_spec())
    write_config(LIBRARY_CONFIG)
    caplog.set_level(logging.DEBUG, logger="untaped")
    Shop.error = HttpTransportError("down", system="shop")
    gather(BookSource.books, refresh=True)()
    assert not [r for r in caplog.records if r.name == "untaped.contracts"]


def test_a_stale_answer_keeps_a_provider_bug_traceback_in_the_debug_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    compose(shelf_spec(), library_spec())
    write_config(LIBRARY_CONFIG)
    gather(BookSource.books)()
    caplog.set_level(logging.DEBUG, logger="untaped")
    Library.error = KeyError("bug")
    [stale] = gather(BookSource.books, refresh=True)()
    assert isinstance(stale, Ok) and stale.stale is not None
    [record] = [r for r in caplog.records if r.name == "untaped.contracts"]
    assert record.exc_info is not None and record.exc_info[1] is Library.error


def test_a_stale_answer_prints_one_stale_warning_when_used_and_counts_nothing() -> None:
    compose(shelf_spec(), library_spec())
    write_config(LIBRARY_CONFIG)
    gather(BookSource.books)()
    Library.error = HttpTransportError("HTTP 503", system="library")
    with recording() as emitted, invocation():
        list(gather(BookSource.books, refresh=True)())
        list(gather(BookSource.books, refresh=True)())  # the same entry again: no second line
        exit_on_counted_failure()  # a stale answer is not a failure

    [stale] = emitted
    [path] = _cache_files()
    assert isinstance(stale, StaleData)
    assert (stale.plugin, stale.cache, stale.key) == (
        "library",
        "shelf.book_source_books",
        _stamp(path)["key"],
    )
    assert stale.error is not None and stale.error.category == "unavailable"
    assert stale.message.startswith("library: refresh failed (HTTP 503)")
    assert stale.hint is None  # the answer cache's reader has no option to suggest


def test_fresh_missing_and_cached_only_answers_print_no_stale_line() -> None:
    compose(shelf_spec(), library_spec())
    write_config(LIBRARY_CONFIG)
    with recording() as emitted, invocation():
        list(gather(BookSource.books, refresh=False)())  # nothing stored: skipped
        list(gather(BookSource.books)())  # a miss, filled live
        list(gather(BookSource.books)())  # a fresh hit
        Library.error = HttpTransportError("HTTP 503", system="library")
        list(gather(BookSource.books, refresh=False)())  # cached only, by request
        _cache_files()[0].unlink()
        list(gather(BookSource.books)())  # a miss that fails

    assert [each for each in emitted if isinstance(each, StaleData)] == []


def test_a_failed_live_call_with_no_cached_answer_fails() -> None:
    compose(shelf_spec(), library_spec())
    write_config(LIBRARY_CONFIG)
    Library.error = HttpTransportError("HTTP 503", system="library")
    [library] = gather(BookSource.books)()
    assert isinstance(library, Failed)


def test_s20_a_changed_model_misses_the_cache_and_nothing_errors() -> None:
    """S20: the cached answer's type differs, so the next call refreshes."""
    compose(shelf_spec(), library_spec())
    write_config(LIBRARY_CONFIG)
    gather(BookSource.books)()
    [path] = _cache_files()
    _rewrite(path, stamp={**_stamp(path), "type": "0" * 16})
    [answer] = gather(BookSource.books)()
    assert isinstance(answer, Ok)
    assert Library.calls == 2
    _rewrite(path, value=[{"title": 5}])
    [answer] = gather(BookSource.books)()
    assert isinstance(answer, Ok)
    assert Library.calls == 3


def test_an_expired_entry_is_refreshed() -> None:
    compose(shelf_spec(), library_spec())
    write_config(LIBRARY_CONFIG)
    gather(BookSource.books)()
    [path] = _cache_files()
    stored = _stamp(path)
    stored["refreshed_at"] = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    _rewrite(path, stamp=stored)
    gather(BookSource.books)()
    assert Library.calls == 2


@pytest.mark.usefixtures("home_run")
def test_the_cache_is_per_provider_settings() -> None:
    compose(shelf_spec(), library_spec())
    write_config(
        LIBRARY_CONFIG
        + "profiles:\n"
        + "  work:\n    library: {catalog: https://w.example, volumes: []}\n"
        + "  same:\n    library: {catalog: https://w.example, volumes: []}\n"
    )
    gather(BookSource.books)()
    with child_run(profiles="work"):
        [answer] = gather(BookSource.books)()
    assert isinstance(answer, Ok)
    assert answer.value == []
    assert Library.calls == 2
    with child_run(profiles="same"):
        [answer] = gather(BookSource.books)()
    assert Library.calls == 2  # the provider's settings read the same as work's
    files = _cache_files()
    assert len(files) == 2
    assert {path.parent.name for path in files} == {settings_digest("shelf")}


def test_the_deadline_bounds_a_provider_requests() -> None:
    from untaped.sdk.network.http import _time_left

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


def test_each_set_of_arguments_has_its_own_cache_entry() -> None:
    compose(shelf_spec(), shop_spec())
    ask = gather(BookSource.by_author)
    ask("Herbert")
    ask("Herbert", limit=10)
    ask("Herbert", 10)
    ask("Austen")
    ask("Herbert", limit=3)
    assert Shop.asked == [("Herbert", 10), ("Austen", 10), ("Herbert", 3)]
    assert len(_cache_files()) == 3


def test_one_item_and_a_plain_value_are_validated_strictly_too() -> None:
    compose(shelf_spec(), shop_spec())
    Shop.rows = [Book(title="Dune")]
    [first] = gather(BookSource.first)()
    assert isinstance(first, Ok)
    assert first.value.source == Source(plugin="shop", kind="shelf.book")
    Shop.rows = [Book.model_construct(title="Dune", pages="many")]
    [first] = gather(BookSource.first)()
    assert isinstance(first, Skipped)
    assert first.reason == "invalid-item"
    Shop.total = 3
    [count] = gather(BookSource.count)()
    assert isinstance(count, Ok) and count.value == 3
    Shop.total = "3"
    [count] = gather(BookSource.count)()
    assert isinstance(count, Skipped)
    assert count.reason == "invalid-item"


def test_needs_names_methods_of_the_same_contract() -> None:
    from untaped.sdk.contracts import Contract

    class Catalogue(Contract):
        def entries(self) -> list[Book]:
            raise NotImplementedError

    with pytest.raises(TypeError, match="not a book_source method"):
        gather(BookSource.books, needs=[Catalogue.entries])


def test_a_cache_entry_names_the_provider_own_record_shape() -> None:
    """S20 for a bridged provider: its record model's shape is part of the entry's schema."""
    import dataclasses

    from core_test_support.contracts.support import SpecialVolume
    from untaped.sdk.contracts._cache import _typing, _typing_of, return_type
    from untaped.sdk.contracts._declare import binding_of

    provider = Library()
    compose(shelf_spec(), library_spec(provider))
    write_config(LIBRARY_CONFIG)
    gather(BookSource.books)()
    binding = binding_of(provider)
    assert binding is not None
    hint = return_type(binding, "books")
    [path] = _cache_files()
    stored = _stamp(path)["type"]
    assert stored == _typing(binding, hint).shape
    assert stored != _typing_of(hint).shape
    assert stored != _typing(dataclasses.replace(binding, item=SpecialVolume), hint).shape


def test_a_cache_that_cannot_be_written_keeps_the_live_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*_: object, **__: object) -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr("untaped.sdk.storage.data_file.atomic_write", refuse)
    compose(shelf_spec(), library_spec())
    write_config(LIBRARY_CONFIG)
    [library] = gather(BookSource.books)()
    assert isinstance(library, Ok)
    assert [book.title for book in library.value] == ["Dune", "Emma"]
    assert _cache_files() == []


def test_an_answer_that_cannot_be_dumped_is_served_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    compose(shelf_spec(), shop_spec())
    write_config(LIBRARY_CONFIG)
    caplog.set_level(logging.DEBUG, logger="untaped")
    Shop.rows = [Book(title="Dune"), object()]
    [shop] = gather(BookSource.books)()
    assert isinstance(shop, Ok)
    assert shop.invalid == ("row 2: expected a Book, got object",)
    assert _cache_files() == []
    [record] = [r for r in caplog.records if r.name == "untaped.cache"]
    assert record.exc_info is not None and isinstance(record.exc_info[1], ValueError)


def test_a_provider_whose_ready_raises_logs_its_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    error = RuntimeError("boom")

    class Broken(Shop):
        def ready(self) -> None:
            raise error

    compose(shelf_spec(), PluginSpec(name="shop", provides={"shelf": lambda: (Broken(),)}))
    caplog.set_level(logging.DEBUG, logger="untaped")
    with pytest.raises(NoProviderReady, match="ready\\(\\) raised RuntimeError: boom"):
        gather(BookSource.books)()
    [record] = [r for r in caplog.records if r.name == "untaped.contracts"]
    assert record.exc_info is not None and record.exc_info[1] is error


def test_self_http_is_the_profiles_client_bounded_by_the_deadline() -> None:
    import httpx
    import respx

    seen: list[object] = []

    class Online(Library):
        def books(self) -> list[Book]:
            response = self.http.get("https://library.example/books")
            return [self.to_book(Volume(id=1, name=response.json()["name"]))]

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions["timeout"]["read"])
        return httpx.Response(200, json={"name": "Dune"})

    provider = Online()
    compose(shelf_spec(), library_spec(provider))
    write_config(LIBRARY_CONFIG + "untaped:\n  http:\n    timeout_seconds: 40\n")
    with respx.mock() as mock:
        mock.get("https://library.example/books").mock(side_effect=record)
        [answer] = gather(BookSource.books, refresh=True, deadline=5)()
        gather(BookSource.books, refresh=True)()
    assert isinstance(answer, Ok)
    assert answer.value[0].title == "Dune"
    assert seen[0] is not None and 0 < seen[0] <= 5  # type: ignore[operator]
    assert seen[1] == 40
    assert provider.http is provider.http


def test_own_reads_back_the_providers_record() -> None:
    from untaped.sdk.contracts._declare import binding_of

    provider = Library()
    compose(shelf_spec(), library_spec(provider))
    write_config(LIBRARY_CONFIG)
    [answer] = gather(BookSource.books)()
    assert isinstance(answer, Ok)
    dune = answer.value[0]
    assert provider.own(dune) == Volume(id=1, name="Dune")
    with pytest.raises(UntapedError, match="not issued by library"):
        provider.own(Book(title="Dune", source=Source(plugin="shop", kind="shelf.book")))
    reshaped = dune.model_copy(
        update={"source": Source(plugin="library", kind="library.volume", record={"id": "x"})}
    )
    with pytest.raises(UntapedError, match="no longer reads") as err:
        provider.own(reshaped)
    assert binding_of(provider) is not None
    assert err.value.hint == "ask library for it again; its record shape changed since"


@dataclass
class _Pair:
    left: int
    right: int


class _Tag(BaseModel):
    name: str = Field(alias="tagName")


def _tag(name: str) -> _Tag:
    return _Tag.model_validate({"tagName": name})


class _TagSource(Contract):
    def tags(self) -> list[_Tag]:
        raise NotImplementedError

    def one(self) -> _Tag:
        raise NotImplementedError

    def token(self) -> SecretStr:
        raise NotImplementedError

    def pairs(self) -> list[_Pair]:
        raise NotImplementedError


class _Tagger(_TagSource):
    def pairs(self) -> list[_Pair]:
        return [_Pair(1, 2)]

    def token(self) -> SecretStr:
        return SecretStr("s3cret")

    def tags(self) -> list[_Tag]:
        return [_tag("a"), "oops"]  # type: ignore[list-item]  # a bad row

    def one(self) -> _Tag:
        return "oops"  # type: ignore[return-value]  # a bad answer


def test_a_listing_drops_a_bad_row_where_a_bad_answer_fails_whole() -> None:
    compose(
        PluginSpec(name="tags", contracts=lambda: (_TagSource,)),
        PluginSpec(name="tagger", provides={"tags": lambda: (_Tagger(),)}),
    )
    [listed] = gather(_TagSource.tags)()
    assert isinstance(listed, Ok)
    assert listed.value == [_tag("a")]
    assert listed.invalid[0].startswith("row 2:")
    [whole] = gather(_TagSource.one)()
    assert isinstance(whole, Skipped)
    assert whole.reason == "invalid-item"


def test_plain_answers_keep_secrets_aliases_and_dataclasses() -> None:
    compose(
        PluginSpec(name="tags", contracts=lambda: (_TagSource,)),
        PluginSpec(name="tagger", provides={"tags": lambda: (_Tagger(),)}),
    )
    [token] = gather(_TagSource.token)()
    assert isinstance(token, Ok)
    assert token.value.get_secret_value() == "s3cret"
    [listed] = gather(_TagSource.tags)()
    assert isinstance(listed, Ok)
    assert listed.value[0].name == "a"
    [pairs] = gather(_TagSource.pairs)()
    assert isinstance(pairs, Ok)
    assert pairs.value == [_Pair(1, 2)]


def test_plugins_asks_only_those_providers() -> None:
    compose(shelf_spec(), library_spec(), shop_spec(), kiosk_spec())
    Shop.rows = [Book(title="Dune")]

    answers = gather(BookSource.books, plugins={"shop"})()

    assert [answer.plugin for answer in answers] == ["shop"]
    assert Library.calls == 0


def test_progress_counts_the_providers_as_they_answer_on_the_calling_thread() -> None:
    import inspect
    import threading

    updates: list[tuple[str | None, int | None, int | None]] = []
    threads: set[int] = set()

    class Recorder:
        def update(
            self,
            message: str | None = None,
            *,
            phase: str | None = None,
            done: int | None = None,
            total: int | None = None,
        ) -> None:
            updates.append((message, done, total))
            threads.add(threading.get_ident())

    compose(shelf_spec(), library_spec(), shop_spec(), kiosk_spec())
    write_config(LIBRARY_CONFIG)
    gather(BookSource.books, progress=Recorder())()
    assert updates[0] == (None, 0, 3)
    assert {message for message, _, _ in updates[1:]} == {"kiosk", "library", "shop"}
    assert [(done, total) for _, done, total in updates] == [(0, 3), (1, 3), (2, 3), (3, 3)]
    assert threads == {threading.get_ident()}
    assert "progress" not in inspect.signature(Library.books).parameters


class _Composer(BookSource):
    """A provider that composes the root from inside its call, one of three ways."""

    def __init__(self, how: str) -> None:
        self.how = how

    def books(self) -> list[Book]:
        if self.how == "compose_root":
            bootstrap.compose_root(candidates=[])
        elif self.how == "composed_with":
            with bootstrap.composed_with([]):
                pass
        else:
            bootstrap.build_root_app(candidates=[])
        return []


@pytest.mark.parametrize("beside", [True, False], ids=["beside-another", "alone"])
@pytest.mark.parametrize("how", ["compose_root", "composed_with", "build_root_app"])
def test_composing_inside_a_contract_call_fails_that_provider(
    how: str, beside: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S5: the composition is process-global, so a provider never swaps it under its neighbours.

    Alone, ``bounded_map`` calls the provider inline; beside another, in a thread.
    """
    composer = PluginSpec(name="composer", provides={"shelf": lambda: (_Composer(how),)})
    composed = compose(shelf_spec(), composer, *([shop_spec()] if beside else []))
    reregistered: list[None] = []
    with monkeypatch.context() as patch:
        patch.setattr(bootstrap, "_reregister", lambda: reregistered.append(None))
        [failed, *others] = gather(BookSource.books, refresh=True)()
    assert isinstance(failed, Failed) and failed.reason == "raised"
    assert "composer's BookSource.books composed the root inside a contract call" in str(
        failed.error
    )
    assert all(isinstance(other, Ok) for other in others) and len(others) == int(beside)
    assert reregistered == []  # nothing re-registered the settings under the neighbours
    assert bootstrap.composition() is composed
    bootstrap.compose_root(candidates=[])  # outside any contract call it works as before
