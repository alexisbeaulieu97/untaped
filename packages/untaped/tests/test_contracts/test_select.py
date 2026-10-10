"""``select_one`` (design §3 rules 1-5, S3, S4, S6, S8) and ``convert``."""

from __future__ import annotations

import pytest

from test_contracts.support import (
    LIBRARY_CONFIG,
    Book,
    BookSource,
    SpecialVolume,
    Volume,
    compose,
    kiosk_spec,
    library_spec,
    shelf_spec,
    shop_spec,
    write_config,
)
from untaped.contracts import (
    Ambiguous,
    Answer,
    Answers,
    Failed,
    NotFound,
    Ok,
    Skipped,
    Source,
    convert,
    select_one,
)
from untaped.errors import ConfigError, ExitCode, HttpTransportError, UntapedError, UsageError
from untaped.pipe import PipeEnvelope
from untaped.plugins.registry import PluginSpec


def _answers(*items: Answer[list[Book]]) -> Answers[list[Book]]:
    return Answers(items, owner="shelf", contract="book_source", method="books")


def _ok(plugin: str, *titles: str, rank: int | None = None, stale: bool = False) -> Ok[list[Book]]:
    books = [Book(title=title) for title in titles]
    failure = Failed(plugin, HttpTransportError("HTTP 503", system=plugin), rank) if stale else None
    return Ok(plugin, books, rank, stale=failure)


def _failed(plugin: str, rank: int | None = None, error: UntapedError | None = None) -> Failed:
    return Failed(plugin, error or HttpTransportError("HTTP 503", system=plugin), rank)


def _dune(book: Book) -> bool:
    return book.title == "Dune"


def test_one_match_is_the_answer() -> None:
    picked = select_one(_answers(_ok("github", "Emma"), _ok("gitlab", "Dune")), _dune)
    assert picked.title == "Dune"


def test_no_match_is_not_found_naming_who_was_not_asked() -> None:
    answers = _answers(_ok("github", "Emma"), Skipped("gitlab", "not-configured", "set gitlab.url"))
    with pytest.raises(NotFound, match=r"not asked: gitlab \(not-configured\)") as err:
        select_one(answers, _dune)
    assert err.value.exit_code == ExitCode.USAGE


def test_s3_two_unranked_matches_are_ambiguous_with_the_rank_hint() -> None:
    answers = _answers(_ok("github", "Dune"), _ok("gitlab", "Dune"))
    with pytest.raises(Ambiguous) as err:
        select_one(answers, _dune)
    assert err.value.exit_code == ExitCode.USAGE
    assert err.value.hint == "untaped plugin rank shelf.book_source books github gitlab"
    ranked = _answers(_ok("gitlab", "Dune", rank=0), _ok("github", "Dune"))
    assert select_one(ranked, _dune) is ranked[0].value[0]  # type: ignore[union-attr]


def test_several_matches_from_one_provider_are_ambiguous() -> None:
    with pytest.raises(Ambiguous, match="2 items from github"):
        select_one(_answers(_ok("github", "Dune", "Dune")), _dune)


def test_the_highest_ranked_match_wins_over_unranked_ones() -> None:
    answers = _answers(
        _ok("gitlab", "Dune", rank=1), _ok("github", "Dune", rank=0), _ok("x", "Dune")
    )
    assert select_one(answers, _dune) is answers[1].value[0]  # type: ignore[union-attr]


def test_s6_with_nothing_ranked_any_failure_blocks_with_its_own_error() -> None:
    expired = ConfigError("token rejected", category="auth", system="gitlab")
    answers = _answers(_ok("github", "Dune"), _failed("gitlab", error=expired))
    with pytest.raises(ConfigError) as err:
        select_one(answers, _dune)
    assert err.value is expired
    assert err.value.exit_code == ExitCode.ENVIRONMENT


def test_s4_a_match_ranked_above_every_failure_wins_with_a_warning(
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers = _answers(_ok("gitlab", "Dune", rank=0), _failed("github", rank=1))
    assert select_one(answers, _dune).title == "Dune"
    assert "github failed" in capsys.readouterr().err
    below = _answers(_failed("github", rank=0), _ok("gitlab", "Dune", rank=1))
    with pytest.raises(HttpTransportError):
        select_one(below, _dune)
    unranked_failure = _answers(_ok("gitlab", "Dune", rank=0), _failed("github"))
    assert select_one(unranked_failure, _dune).title == "Dune"


def test_s4_a_stale_listing_confirms_a_match_never_an_absence() -> None:
    confirmed = _answers(_ok("github", "Dune", stale=True), _ok("gitlab", "Emma"))
    assert select_one(confirmed, _dune).title == "Dune"
    absent = _answers(_ok("github", "Emma", stale=True), _ok("gitlab", "Emma"))
    with pytest.raises(HttpTransportError) as err:
        select_one(absent, _dune)
    assert err.value.exit_code == ExitCode.UNAVAILABLE
    ranked = _answers(_ok("gitlab", "Dune", rank=0), _ok("github", "Emma", rank=1, stale=True))
    assert select_one(ranked, _dune).title == "Dune"


def test_s8_invalid_rows_skip_an_unranked_provider_and_fail_a_ranked_one(
    capsys: pytest.CaptureFixture[str],
) -> None:
    bad = Ok("gitlab", [Book(title="Dune")], invalid=("row 2: title: bad",))
    assert select_one(_answers(_ok("github", "Dune"), bad), _dune).title == "Dune"
    assert "gitlab wasn't asked (invalid-item)" in capsys.readouterr().err
    ranked_bad = Ok("gitlab", [], 0, invalid=("row 2: title: bad",))
    with pytest.raises(ConfigError, match="gitlab returned 1 invalid") as err:
        select_one(_answers(ranked_bad, _ok("github", "Dune")), _dune)
    assert err.value.hint == "upgrade untaped-gitlab"
    assert err.value.exit_code == ExitCode.ENVIRONMENT


def test_a_skip_other_than_not_configured_is_warned(capsys: pytest.CaptureFixture[str]) -> None:
    answers = _answers(
        _ok("github", "Dune"),
        Skipped("gitlab", "not-configured", "set gitlab.url"),
        Skipped("gitea", "no-cache", "no cached answer"),
    )
    select_one(answers, _dune)
    err = capsys.readouterr().err
    assert "gitea wasn't asked (no-cache)" in err
    assert "gitlab" not in err


def _envelope(kind: str | None, record: dict[str, object]) -> PipeEnvelope:
    return PipeEnvelope(kind=kind, record=record, lineno=1)


def test_convert_reads_the_owner_kind_directly() -> None:
    compose(shelf_spec(), kiosk_spec())
    book = convert(BookSource.to_book, _envelope("shelf.book", {"title": "Dune"}))
    assert book == Book(title="Dune")
    assert book.source is None


def test_convert_sends_a_provider_kind_through_its_bridge() -> None:
    compose(shelf_spec(), library_spec(), shop_spec())
    write_config(LIBRARY_CONFIG)
    book = convert(BookSource.to_book, _envelope("library.volume", {"id": 3, "name": "Dune"}))
    assert book.title == "Dune"
    assert book.source == Source(
        plugin="library", kind="library.volume", record={"id": 3, "name": "Dune"}
    )
    special = convert(
        BookSource.to_book,
        _envelope("library.special_volume", {"id": 4, "name": "Emma", "signed": True}),
    )
    assert special.source is not None
    assert special.source.kind == "library.special_volume"


def test_convert_refuses_kind_less_unknown_and_invalid_records() -> None:
    compose(shelf_spec(), library_spec(), kiosk_spec())
    write_config(LIBRARY_CONFIG)
    with pytest.raises(UsageError, match="needs a kind"):
        convert(BookSource.to_book, _envelope(None, {"title": "Dune"}))
    with pytest.raises(UsageError, match=r"no installed provider reads untaped\.profile"):
        convert(BookSource.to_book, _envelope("untaped.profile", {"name": "x"}))
    with pytest.raises(ConfigError, match=r"invalid library\.volume record") as err:
        convert(BookSource.to_book, _envelope("library.volume", {"id": "3", "name": "Dune"}))
    assert err.value.exit_code == ExitCode.FAILURE


class _Archive(BookSource[SpecialVolume]):
    def to_book(self, item: SpecialVolume) -> Book:
        return Book(title=f"signed {item.name}" if item.signed else item.name)

    def books(self) -> list[Book]:
        return []


class _Copycat(BookSource[Volume]):
    def to_book(self, item: Volume) -> Book:
        return Book(title=item.name)

    def books(self) -> list[Book]:
        return []


def test_convert_picks_the_most_derived_reader_and_refuses_a_tie() -> None:
    archive = PluginSpec(name="archive", provides={"shelf": lambda: (_Archive(),)})
    compose(shelf_spec(), library_spec(), archive)
    write_config(LIBRARY_CONFIG)
    signed = _envelope("library.special_volume", {"id": 4, "name": "Emma", "signed": True})
    book = convert(BookSource.to_book, signed)
    assert book.title == "signed Emma"
    assert book.source is not None and book.source.plugin == "archive"
    plain = convert(BookSource.to_book, _envelope("library.volume", {"id": 1, "name": "A"}))
    assert plain.source is not None and plain.source.plugin == "library"
    copycat = PluginSpec(name="copycat", provides={"shelf": lambda: (_Copycat(),)})
    compose(shelf_spec(), library_spec(), copycat)
    with pytest.raises(ConfigError, match="duplicate-kind") as err:
        convert(BookSource.to_book, _envelope("library.volume", {"id": 1, "name": "A"}))
    assert err.value.exit_code == ExitCode.ENVIRONMENT
