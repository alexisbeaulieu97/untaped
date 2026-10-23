"""The answer cache on ``Cache.serve()``, and when a stale answer's line is said (#537 B3)."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import pytest

from core_test_support.contracts.support import (
    LIBRARY_CONFIG,
    Book,
    BookSource,
    Library,
    Shop,
    Volume,
    compose,
    library_spec,
    shelf_spec,
    shop_spec,
)
from core_test_support.contracts.support import write_config as configure
from untaped.runtime.contracts.registry import Provider, offers
from untaped.runtime.sink import diagnostics_scope, recording
from untaped.sdk.contracts import Failed, Ok, Skipped, gather, select_one
from untaped.sdk.contracts._declare import contract_of
from untaped.sdk.contracts._gather import Answers
from untaped.sdk.diagnostic import Diagnostic, Failure, Message, Progress, StaleData
from untaped.sdk.errors import HttpTransportError, UntapedError
from untaped.sdk.storage.cache import RETRY_AFTER, Cache, Filled
from untaped.sdk.testing import CacheClock, ProgressClock, cache_clock, progress_clock

DOWN = HttpTransportError("HTTP 503", system="library")


@pytest.fixture
def clock() -> Iterator[CacheClock]:
    with cache_clock() as clock:
        yield clock


def _entries() -> list[Path]:
    root = Path(os.environ["HOME"]) / ".untaped" / "plugins"
    return sorted(root.rglob("*.v*.json"))


def _stamp(entry: Path) -> dict[str, Any]:
    stamp: dict[str, Any] = json.loads(entry.read_text().splitlines()[0])["untaped"]
    return stamp


def _restamp(entry: Path, **changes: object) -> None:
    head, value = entry.read_text().splitlines()
    stamp = {**json.loads(head)["untaped"], **changes}
    entry.write_text(json.dumps({"untaped": stamp}) + "\n" + value + "\n")


def _stale(emitted: list[Diagnostic]) -> list[StaleData]:
    return [each for each in emitted if isinstance(each, StaleData)]


def _stale_library(clock: CacheClock) -> None:
    """Library's answer stored, then past its max age, with its next live call failing."""
    compose(shelf_spec(), library_spec(), shop_spec())
    configure(LIBRARY_CONFIG)
    gather(BookSource.books)()
    clock.advance(timedelta(hours=2))
    Library.error = DOWN


def _titled(title: str) -> object:
    return lambda book: book.title == title


# --------------------------------------------------------------------------- #
# When the line is said (decision 5)
# --------------------------------------------------------------------------- #


def test_a_stale_absence_fails_with_the_providers_error_alone(clock: CacheClock) -> None:
    _stale_library(clock)
    with recording() as emitted, diagnostics_scope():
        answers = gather(BookSource.books)()
        with pytest.raises(UntapedError) as caught:
            select_one(answers, _titled("Ulysses"))  # type: ignore[arg-type]
        list(answers)  # read again afterwards: the failure said it
    assert caught.value is DOWN
    assert _stale(emitted) == []


def test_a_stale_match_used_says_its_line_once_and_one_unused_says_nothing(
    clock: CacheClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stale_library(clock)
    Shop.rows = [Book(title="Emma")]
    with recording() as emitted, diagnostics_scope():
        found = select_one(gather(BookSource.books)(), _titled("Dune"))  # type: ignore[arg-type]
    assert found.title == "Dune"
    [line] = _stale(emitted)
    assert line.plugin == "library"

    monkeypatch.setattr("untaped.sdk.contracts._gather.ranking", lambda *_: ("shop", "library"))
    with recording() as emitted, diagnostics_scope():
        found = select_one(gather(BookSource.books)(), _titled("Emma"))  # type: ignore[arg-type]
    assert found.source is not None and found.source.plugin == "shop"
    assert _stale(emitted) == []


def test_a_match_ranked_above_a_stale_absence_warns_without_a_stale_line(
    clock: CacheClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stale_library(clock)
    Shop.rows = [Book(title="Ulysses")]
    monkeypatch.setattr("untaped.sdk.contracts._gather.ranking", lambda *_: ("shop", "library"))
    with recording() as emitted, diagnostics_scope():
        found = select_one(gather(BookSource.books)(), _titled("Ulysses"))  # type: ignore[arg-type]
    assert found.title == "Ulysses"
    assert _stale(emitted) == []
    [warning] = [each for each in emitted if isinstance(each, Message)]
    assert warning.message == "library failed (HTTP 503); using a match ranked above it"


def test_a_stale_line_a_warning_left_is_said_when_the_same_answers_use_its_match(
    clock: CacheClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stale_library(clock)
    Shop.rows = [Book(title="Ulysses")]
    monkeypatch.setattr("untaped.sdk.contracts._gather.ranking", lambda *_: ("shop", "library"))
    with recording() as emitted, diagnostics_scope():
        answers = gather(BookSource.books)()  # one ask for two names, as `--repo a --repo b`
        select_one(answers, _titled("Ulysses"))  # type: ignore[arg-type]
        assert _stale(emitted) == []
        found = select_one(answers, _titled("Dune"))  # type: ignore[arg-type]
    assert found.title == "Dune"
    [line] = _stale(emitted)
    assert line.plugin == "library"


def test_a_stale_listing_says_its_line_on_first_iteration_or_index_and_never_twice(
    clock: CacheClock,
) -> None:
    _stale_library(clock)
    with recording() as emitted, diagnostics_scope():
        answers = gather(BookSource.books)()
        assert len(answers) == 2
        answers.rank_command()
        assert _stale(emitted) == []
        list(answers)
        list(answers)
        assert len(_stale(emitted)) == 1
        indexed = gather(BookSource.books)()
        indexed[0]
        list(indexed)
    assert len(_stale(emitted)) == 1  # the sink drops a repeat of the same entry's line


def test_an_answer_fetched_and_never_used_says_nothing(clock: CacheClock) -> None:
    _stale_library(clock)
    with recording() as emitted, diagnostics_scope():
        answers = gather(BookSource.books)()
    assert _stale(emitted) == []
    [library, _shop] = answers._unsaid()
    assert isinstance(library, Ok) and library.stale is not None  # still readable


def test_take_stale_hands_the_lines_over_once(clock: CacheClock) -> None:
    _stale_library(clock)
    with recording() as emitted, diagnostics_scope():
        answers = gather(BookSource.books)()
        [line] = answers.take_stale()
        assert answers.take_stale() == ()
        list(answers)
    assert line.plugin == "library"
    assert _stale(emitted) == []
    hand_built: Answers[list[Book]] = Answers([], owner="shelf", contract="c", method="m")
    assert hand_built.take_stale() == ()


def test_two_threads_reading_one_answers_say_its_line_once(clock: CacheClock) -> None:
    _stale_library(clock)
    with recording() as emitted, diagnostics_scope():
        answers = gather(BookSource.books)()
        taken: list[tuple[StaleData, ...]] = []
        threads = [
            threading.Thread(target=lambda: taken.append(answers.take_stale())) for _ in range(8)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    assert sorted(len(lines) for lines in taken) == [0] * 7 + [1]
    assert _stale(emitted) == []


def test_a_provider_called_outside_gather_says_its_line_at_the_call(clock: CacheClock) -> None:
    _stale_library(clock)
    with recording() as emitted, diagnostics_scope():
        books = _library_provider().instance.books()  # type: ignore[attr-defined]
    assert [book.title for book in books] == ["Dune", "Emma"]
    [line] = _stale(emitted)
    assert (line.plugin, line.cache) == ("library", "shelf.book_source_books")


def test_a_provider_exception_reaches_the_caller_with_its_own_cause() -> None:
    compose(shelf_spec(), library_spec())
    configure(LIBRARY_CONFIG)
    root = KeyError("root")
    try:
        raise RuntimeError("boom") from root
    except RuntimeError as exc:
        Library.error = exc
    with pytest.raises(RuntimeError) as caught:
        _library_provider().instance.books()  # type: ignore[attr-defined]
    assert caught.value is Library.error
    assert caught.value.__cause__ is root


@pytest.mark.parametrize("failure", [RuntimeError("boom"), DOWN])
def test_a_provider_that_raises_drops_the_stale_lines_of_its_own_caches(
    clock: CacheClock, failure: Exception
) -> None:
    extra = Cache("library", "extra", label="extra", max_age=timedelta(hours=1))
    filled: list[bool] = []

    def fill(_key: str, _task: object) -> Filled:
        if filled:
            raise DOWN
        filled.append(True)
        return Filled()

    class Layered(Library):
        def books(self) -> list[Book]:
            extra.serve({"k": "k"}, refresh=None, fill=fill, read=lambda: None)
            if filled and Library.error is not None:
                raise Library.error
            return super().books()

    compose(shelf_spec(), library_spec(Layered()))
    configure(LIBRARY_CONFIG)
    extra.serve({"k": "k"}, refresh=None, fill=fill, read=lambda: None)
    clock.advance(timedelta(hours=2))
    Library.error = failure
    with recording() as emitted, diagnostics_scope(), pytest.raises(type(failure)):
        _library_provider().instance.books()  # type: ignore[attr-defined]
    assert _stale(emitted) == []  # the raise says it


def test_an_interrupted_refresh_suggests_no_cached_option(clock: CacheClock) -> None:
    class Halting(Library):
        halt: ClassVar[bool] = False

        def books(self) -> list[Book]:
            if type(self).halt:
                raise KeyboardInterrupt
            return super().books()

    compose(shelf_spec(), library_spec(Halting()))
    configure(LIBRARY_CONFIG)
    gather(BookSource.books)()
    clock.advance(timedelta(hours=2))
    Halting.halt = True
    with recording() as emitted, diagnostics_scope(), pytest.raises(KeyboardInterrupt):
        _library_provider().instance.books()  # type: ignore[attr-defined]
    [error] = [each for each in emitted if isinstance(each, Failure)]
    assert error.message == "interrupted while refreshing library"
    assert error.hint is None  # the answer cache's reader has no --cached to suggest


# --------------------------------------------------------------------------- #
# The entry and serve() (F9)
# --------------------------------------------------------------------------- #


def test_the_line_names_the_provider_the_owners_cache_and_the_entry(clock: CacheClock) -> None:
    _stale_library(clock)
    with recording() as emitted, diagnostics_scope():
        list(gather(BookSource.books)())
    [line] = _stale(emitted)
    [entry] = [each for each in _entries() if "library" in each.name]
    assert line.plugin == "library"
    assert line.cache == "shelf.book_source_books"
    assert line.key == _stamp(entry)["key"]
    assert line.key.startswith("library:") and len(line.key) == len("library:") + 64


def test_11_0s_entry_and_1cs_path_are_misses() -> None:
    compose(shelf_spec(), library_spec())
    configure(LIBRARY_CONFIG)
    gather(BookSource.books)()
    [entry] = _entries()
    key = _stamp(entry)["key"]
    segment = entry.name.partition(".v")[0]
    home = Path(os.environ["HOME"]) / ".untaped/plugins"
    legacy = home / "shelf/caches/book_source_books/default"
    legacy.mkdir(parents=True)
    (legacy / f"{segment}.json").write_text(
        json.dumps({"untaped": "1", "key": key, "refreshed_at": datetime.now().isoformat()})
    )
    (legacy / f"{segment}.answer.json").write_text(
        json.dumps({"untaped": "2", "schema": "0" * 64, "value": []})
    )
    old = home / "library/cache/shelf.book_source.books/default"
    old.mkdir(parents=True)
    (old / f"{key.removeprefix('library:')}.json").write_text(
        json.dumps({"untaped": "1", "key": key, "value": []})
    )
    entry.unlink()
    [library] = gather(BookSource.books)()
    assert Library.calls == 2
    assert isinstance(library, Ok) and [book.title for book in library.value] == ["Dune", "Emma"]


def test_a_changed_type_refills_under_auto_and_is_no_cache_under_cached() -> None:
    compose(shelf_spec(), library_spec())
    configure(LIBRARY_CONFIG)
    gather(BookSource.books)()
    [entry] = _entries()
    _restamp(entry, type="0" * 16)
    [library] = gather(BookSource.books, refresh=False)()
    assert isinstance(library, Skipped) and library.reason == "no-cache"
    assert Library.calls == 1
    [library] = gather(BookSource.books)()
    assert isinstance(library, Ok)
    assert Library.calls == 2


def test_an_entry_another_process_replaces_after_the_read_serves_what_was_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The freshness and the value are one read of one file: what was judged is served."""
    compose(shelf_spec(), library_spec())
    configure(LIBRARY_CONFIG)
    gather(BookSource.books)()
    [entry] = _entries()
    load = Cache._load

    def load_then_replaced(self: Cache[Any], *args: Any, **kwargs: Any) -> Any:
        kept = load(self, *args, **kwargs)
        head = entry.read_text().splitlines()[0]
        entry.write_text(head + "\n" + json.dumps([{"title": "Other"}]) + "\n")
        return kept

    monkeypatch.setattr(Cache, "_load", load_then_replaced)
    answer = _library_provider().instance.books()  # type: ignore[attr-defined]
    assert [book.title for book in answer] == ["Dune", "Emma"]
    assert Library.calls == 1


def test_two_threads_asking_one_stale_answer_make_one_live_call(clock: CacheClock) -> None:
    inside, release = threading.Event(), threading.Event()

    class Slow(Library):
        def books(self) -> list[Book]:
            inside.set()
            release.wait(10)
            return super().books()

    compose(shelf_spec(), library_spec(Slow()))
    configure(LIBRARY_CONFIG)
    release.set()
    gather(BookSource.books)()
    clock.advance(timedelta(hours=2))
    inside.clear()
    release.clear()
    answers: list[object] = []
    first = threading.Thread(target=lambda: answers.extend(gather(BookSource.books)()))
    first.start()
    assert inside.wait(10)
    second = threading.Thread(target=lambda: answers.extend(gather(BookSource.books)()))
    second.start()
    release.set()
    first.join(10)
    second.join(10)
    assert Slow.calls == 2  # the first fill, then one refresh for both threads
    assert [isinstance(each, Ok) and each.stale is None for each in answers] == [True, True]


def test_a_failed_refresh_is_remembered_until_its_retry_time(clock: CacheClock) -> None:
    _stale_library(clock)
    list(gather(BookSource.books)())
    assert Library.calls == 2
    with recording() as emitted, diagnostics_scope():
        [library, _shop] = gather(BookSource.books)()
    assert Library.calls == 2  # inside the retry window: no call
    assert isinstance(library, Ok) and library.stale is not None
    assert str(library.stale.error) == "HTTP 503"
    [line] = _stale(emitted)
    assert line.retry_at is not None and line.hint is None
    assert not line.message.endswith("or pass --refresh")
    gather(BookSource.books, refresh=True)()
    assert Library.calls == 3
    clock.advance(RETRY_AFTER)
    gather(BookSource.books)()
    assert Library.calls == 4


def test_a_never_refreshed_answer_that_failed_is_asked_again(clock: CacheClock) -> None:
    compose(shelf_spec(), library_spec())
    configure(LIBRARY_CONFIG)
    Library.error = DOWN
    [first] = gather(BookSource.books)()
    [again] = gather(BookSource.books)()
    assert isinstance(first, Failed) and isinstance(again, Failed)
    assert again.error is DOWN
    assert Library.calls == 2


def _ticking(ticks: ProgressClock) -> Library:
    class Ticking(Library):
        def books(self) -> list[Book]:
            ticks.advance(3)  # past the 2 s a progress line waits for
            return super().books()

    return Ticking()


def test_a_first_fill_runs_under_a_task_naming_the_provider() -> None:
    with progress_clock() as ticks:
        compose(shelf_spec(), library_spec(_ticking(ticks)))
        configure(LIBRARY_CONFIG)
        with recording() as emitted, diagnostics_scope():
            gather(BookSource.books)()
    progress = [each for each in emitted if isinstance(each, Progress)]
    assert {each.label for each in progress} == {"Refreshing library (first use)"}
    assert progress[-1].state == "done"


def test_a_failed_refresh_closes_its_task_before_the_line(clock: CacheClock) -> None:
    with progress_clock() as ticks:
        compose(shelf_spec(), library_spec(_ticking(ticks)))
        configure(LIBRARY_CONFIG)
        gather(BookSource.books)()
        clock.advance(timedelta(hours=2))
        Library.error = DOWN
        with recording() as emitted, diagnostics_scope():
            list(gather(BookSource.books)())
    kinds = [
        each.state if isinstance(each, Progress) else "stale"
        for each in emitted
        if isinstance(each, Progress | StaleData)
    ]
    assert kinds[-2:] == ["failed", "stale"]


def test_an_override_calling_its_base_method_is_one_cached_call() -> None:
    class Annotated(Library):
        def books(self) -> list[Book]:
            return [*super().books(), self.to_book(Volume(id=3, name="Notes"))]

    compose(shelf_spec(), library_spec(Annotated()))
    configure(LIBRARY_CONFIG)
    gather(BookSource.books)()
    [answer] = gather(BookSource.books)()
    assert isinstance(answer, Ok)
    assert [book.title for book in answer.value] == ["Dune", "Emma", "Notes"]
    assert Annotated.calls == 1
    assert len(_entries()) == 1


def _library_provider() -> Provider:
    [library] = [
        entry
        for entry in offers(contract_of(BookSource))  # type: ignore[arg-type]
        if isinstance(entry, Provider) and entry.plugin == "library"
    ]
    return library


def test_plugin_check_reports_a_stale_answer_as_its_failed_row_alone() -> None:
    from untaped.core.plugin_check.contracts import _call

    compose(shelf_spec(), library_spec())
    configure(LIBRARY_CONFIG)
    gather(BookSource.books)()
    Library.error = DOWN
    with recording() as emitted, diagnostics_scope():
        row = _call(_library_provider(), "books", "shelf.book_source.books", [], lambda *a: a)
    assert row == ("live", "shelf.book_source.books", "fail", "served a cached answer: HTTP 503")
    assert _stale(emitted) == []
