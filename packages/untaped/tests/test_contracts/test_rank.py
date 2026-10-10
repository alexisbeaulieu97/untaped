"""Contract settings and the ``plugin`` commands around them (design §7, §9, §13; #548)."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import ClassVar

import pytest
from cyclopts import App
from pydantic import BaseModel, Field

from test_contracts.support import (
    LIBRARY_CONFIG,
    Book,
    BookSource,
    Shop,
    compose,
    kiosk_spec,
    library_spec,
    shelf_spec,
    shop_spec,
    write_config,
)
from untaped.bootstrap import build_root_app
from untaped.contracts import Contract, Ok, Skipped, gather, select_one
from untaped.contracts._registry import doctor_rows, ranking
from untaped.errors import ConfigError
from untaped.plugins.registry import PluginSpec
from untaped.records import Record
from untaped.sdk import deprecated, experimental
from untaped.settings import get_config_section, load_settings_section, registered_profile_model
from untaped.testing import CliInvoker, CliResult, provider_candidate

RANKED = """\
profiles:
  default:
    shelf:
      extensions:
        book_source:
          rank:
            books: [shop, library]
"""


def _run(argv: Sequence[str], *specs: PluginSpec) -> CliResult:
    app = build_root_app(candidates=[provider_candidate(spec) for spec in specs])
    return CliInvoker().invoke(app.meta, list(argv))


def _all() -> tuple[PluginSpec, ...]:
    return (shelf_spec(), library_spec(), shop_spec(), kiosk_spec())


def _extensions() -> object:
    return json.loads(_run(["config", "get", "shelf.extensions"], *_all()).stdout)


# --- the injected key -------------------------------------------------------


def test_every_owner_section_has_extensions_and_no_other_section_does() -> None:
    compose(shelf_spec(), library_spec())
    shelf = registered_profile_model("shelf")
    library = registered_profile_model("library")
    assert shelf is not None and "extensions" in shelf.model_fields
    assert library is not None and "extensions" not in library.model_fields


def test_an_owner_with_settings_keeps_them_and_reads_them_as_its_own_model() -> None:
    class ShelfSettings(BaseModel):
        model_config = {"extra": "forbid"}
        aisle: int = 1

    owner = PluginSpec(name="shelf", settings=ShelfSettings, contracts=lambda: (BookSource,))
    compose(owner)
    write_config(RANKED.replace("shelf:\n", "shelf:\n      aisle: 4\n"))
    settings = get_config_section("shelf", ShelfSettings)
    assert isinstance(settings, ShelfSettings)
    assert settings.aisle == 4
    assert ranking("shelf", "book_source", "books") == ("shop", "library")


def test_rankings_come_from_config_and_order_gather() -> None:
    write_config(LIBRARY_CONFIG + RANKED.removeprefix("profiles:\n  default:\n"))
    compose(*_all())
    answers = gather(BookSource.books)()
    assert [(answer.plugin, answer.rank) for answer in answers] == [
        ("shop", 0),
        ("library", 1),
        ("kiosk", None),
    ]


def test_the_environment_overrides_a_ranking() -> None:
    compose(*_all())
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("UNTAPED_SHELF__EXTENSIONS__BOOK_SOURCE__RANK__BOOKS", '["kiosk","shop"]')
        assert ranking("shelf", "book_source", "books") == ("kiosk", "shop")


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("Book_Source: {rank: {books: [shop]}}", "String should match pattern"),
        ("book_source: {rank: {books: [shop, shop]}}", "shop ranked twice"),
        ("book_source: {order: {books: [shop]}}", "Extra inputs are not permitted"),
        ("book_source: {rank: {books: [Shop]}}", "String should match pattern"),
    ],
)
def test_a_malformed_extensions_value_is_a_config_error(body: str, message: str) -> None:
    compose(*_all())
    write_config(f"profiles:\n  default:\n    shelf:\n      extensions:\n        {body}\n")
    with pytest.raises(ConfigError, match=message):
        load_settings_section("shelf")


def test_a_ranking_for_a_missing_contract_method_or_plugin_changes_nothing() -> None:
    """A ranking is grammar-checked only: names that don't exist are no load error."""
    write_config(
        "profiles:\n  default:\n    shelf:\n      extensions:\n"
        "        gone: {rank: {x: [nobody]}}\n"
        "        book_source: {rank: {nope: [shop], books: [gitlab, kiosk]}}\n"
    )
    compose(shelf_spec(), shop_spec(), kiosk_spec())
    Book.model_rebuild()
    answers = gather(BookSource.books)()
    assert [(answer.plugin, answer.rank) for answer in answers] == [("kiosk", 1), ("shop", None)]


def test_a_ranking_in_the_default_profile_works_where_the_provider_isnt_configured() -> None:
    """``library`` ranked first in ``default``; the ``work`` profile doesn't configure it."""
    write_config(
        "active: work\nprofiles:\n  default:\n    shelf:\n      extensions:\n"
        "        book_source: {rank: {books: [library, shop]}}\n"
        "  work: {}\n"
    )
    compose(*_all())
    from test_contracts.support import Kiosk, Shop

    Shop.rows = [Book(title="Dune")]
    Kiosk.rows = [Book(title="Emma")]
    answers = gather(BookSource.books)()
    assert isinstance(answers[0], Skipped)
    assert (answers[0].plugin, answers[0].reason) == ("library", "not-configured")
    book = select_one(answers, lambda each: each.title == "Dune")
    assert book.source is not None and book.source.plugin == "shop"
    assert all(isinstance(answer, Ok | Skipped) for answer in answers)


class _Reserved(BaseModel):
    extensions: dict[str, str] = {}


class _ReservedOldKey(BaseModel):
    renamed_keys: ClassVar[Mapping[str, str]] = {"extensions": "plugins"}
    plugins: dict[str, str] = {}


class _ReservedAlias(BaseModel):
    ext: dict[str, str] = Field(default_factory=dict, alias="caches")


class _ReservedState(BaseModel):
    caches: dict[str, str] = {}


@pytest.mark.parametrize(
    "spec",
    [
        PluginSpec(name="acme", settings=_Reserved),
        PluginSpec(name="acme", state=_ReservedState),
        PluginSpec(name="acme", settings=_ReservedOldKey, contracts=lambda: ()),
        PluginSpec(name="acme", settings=_ReservedOldKey),
        PluginSpec(name="acme", settings=_ReservedAlias),
    ],
    ids=["settings-extensions", "state-caches", "owner-old-key", "old-key", "alias"],
)
def test_a_plugin_declaring_an_injected_key_is_quarantined(spec: PluginSpec) -> None:
    result = compose(spec)
    [record] = result.quarantine
    assert record.reason == "bad-settings-keys"
    assert "untaped injects" in record.detail


# --- plugin rank ------------------------------------------------------------


def test_plugin_rank_writes_the_ranking_and_ranking_none_removes_it() -> None:
    result = _run(["plugin", "rank", "shelf.book_source", "books", "kiosk", "shop"], *_all())
    assert result.exit_code == 0, result.stderr
    assert "key: shelf.extensions\n" in result.stdout
    assert "ranked kiosk > shop" in result.stderr
    assert _extensions() == {"book_source": {"rank": {"books": ["kiosk", "shop"]}}}
    assert ranking("shelf", "book_source", "books") == ("kiosk", "shop")
    _run(["plugin", "rank", "shelf.book_source", "lookup", "shop"], *_all())
    _run(["plugin", "rank", "shelf.book_source", "books"], *_all())
    assert _extensions() == {"book_source": {"rank": {"lookup": ["shop"]}}}
    _run(["plugin", "rank", "shelf.book_source", "lookup"], *_all())
    assert _extensions() == {}


def test_plugin_rank_dry_run_writes_nothing() -> None:
    result = _run(
        ["plugin", "rank", "shelf.book_source", "books", "shop", "--dry-run", "-f", "json"],
        *_all(),
    )
    assert json.loads(result.stdout)["action"] == "planned"
    assert _extensions() == {}


def test_plugin_rank_writes_to_the_root_profile() -> None:
    write_config("profiles:\n  default: {}\n  work: {}\n")
    _run(["--profile", "work", "plugin", "rank", "shelf.book_source", "books", "shop"], *_all())
    result = _run(["--profile", "work", "config", "get", "shelf.extensions"], *_all())
    assert json.loads(result.stdout) == {"book_source": {"rank": {"books": ["shop"]}}}
    assert _extensions() == {}


def test_plugin_rank_keeps_a_plugin_that_doesnt_fill_the_method_with_a_warning() -> None:
    result = _run(["plugin", "rank", "shelf.book_source", "lookup", "gitlab", "shop"], *_all())
    assert result.exit_code == 0
    assert "gitlab doesn't fill shelf.book_source lookup here" in result.stderr
    assert "shop doesn't" not in result.stderr


def test_plugin_rank_removing_what_isnt_there_changes_nothing() -> None:
    write_config("profiles:\n  default: {}\n")
    result = _run(["plugin", "rank", "shelf.book_source", "books", "-f", "json"], *_all())
    assert json.loads(result.stdout)["action"] == "unchanged"
    assert "shelf.extensions.book_source.rank.books unchanged" in result.stderr
    assert "shelf" not in Path(os.environ["UNTAPED_CONFIG"]).read_text()


def test_plugin_rank_reports_unchanged_updated_and_deleted() -> None:
    def action(*argv: str) -> str:
        argv_ = ["plugin", "rank", "shelf.book_source", *argv, "-f", "json"]
        return str(json.loads(_run(argv_, *_all()).stdout)["action"])

    assert action("books", "shop") == "updated"
    assert action("books", "shop") == "unchanged"
    assert action("books") == "deleted"


@pytest.mark.parametrize(
    "stored",
    [
        "book_source: null",
        "book_source: x",
        "book_source: {rank: [a]}",
        "book_source: {rank: null}",
    ],
)
def test_plugin_rank_repairs_a_malformed_stored_value(stored: str) -> None:
    write_config(f"profiles:\n  default:\n    shelf:\n      extensions:\n        {stored}\n")
    result = _run(["plugin", "rank", "shelf.book_source", "books", "shop"], *_all())
    assert result.exit_code == 0, result.stderr
    assert _extensions() == {"book_source": {"rank": {"books": ["shop"]}}}


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["nobody.x", "y"], "'nobody' owns no contract"),
        (["shelf.Bad-Name", "books"], "invalid ranking"),
        (["book_source", "books", "shop"], "name the contract as OWNER.CONTRACT"),
        (["library.book_source", "books", "shop"], "'library' owns no contract"),
        (["shelf.tags", "books", "shop"], "shelf declares no contract 'tags'"),
        (["shelf.book_source", "search", "shop"], "search isn't a method"),
        (["shelf.book_source", "to_book", "library"], "to_book is a bridge"),
        (["shelf.book_source", "books", "shop", "shop"], "shop ranked twice"),
        (["shelf.book_source", "books", "Shop"], "invalid ranking"),
    ],
)
def test_plugin_rank_refuses_what_it_cant_rank(argv: list[str], message: str) -> None:
    result = _run(["plugin", "rank", *argv], *_all())
    assert result.exit_code == 2
    assert message in result.stderr
    assert _extensions() == {}


# --- plugin list --contracts ------------------------------------------------


@experimental
class TagSource(Contract):
    """Tags nobody fills."""

    def tags(self) -> list[str]:
        raise NotImplementedError

    @deprecated(replacement="tags")
    def labels(self) -> list[str]:
        raise NotImplementedError


def _tags_spec() -> PluginSpec:
    return PluginSpec(name="tags", contracts=lambda: (TagSource,))


def test_plugin_list_contracts_lists_every_method_with_who_fills_it() -> None:
    write_config(RANKED)
    result = _run(["plugin", "list", "--contracts", "-f", "json"], *_all(), _tags_spec())
    rows = {(row["contract"], row["method"]): row for row in json.loads(result.stdout)}
    books = rows[("shelf.book_source", "books")]
    assert books == {
        "contract": "shelf.book_source",
        "method": "books",
        "owner": "shelf",
        "stability": "experimental",
        "providers": ["shop", "library", "kiosk"],
        "ranked": ["shop", "library"],
    }
    assert rows[("shelf.book_source", "to_book")]["providers"] == ["library"]
    assert rows[("tags.tag_source", "tags")]["providers"] == []
    assert rows[("tags.tag_source", "labels")]["stability"] == "deprecated"


def test_plugin_list_contracts_survives_broken_owner_settings() -> None:
    write_config(RANKED.replace("[shop, library]", "[shop, shop]"))
    result = _run(["plugin", "list", "--contracts", "-f", "json"], *_all())
    assert result.exit_code == 0
    assert "rankings not shown" in result.stderr
    [books] = [row for row in json.loads(result.stdout) if row["method"] == "books"]
    assert books["ranked"] == []


def test_plugin_list_contracts_with_no_owner_says_so() -> None:
    result = _run(["plugin", "list", "--contracts"], shop_spec())
    assert result.exit_code == 0
    assert "No installed plugin declares a contract." in result.stdout + result.stderr


# --- plugin schema ----------------------------------------------------------


def test_plugin_schema_prints_a_kinds_json_schema() -> None:
    result = _run(["plugin", "schema", "shelf.book"], *_all())
    schema = json.loads(result.stdout)
    assert schema["$id"] == schema["title"] == "shelf.book"
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["required"] == ["title"]
    assert set(schema["properties"]) == {"source", "title", "pages"}


def test_plugin_schema_finds_a_kind_only_a_plugin_app_declares() -> None:
    def factory() -> App:
        class _Gizmo(Record, kind="gizmo.thing"):
            size: int

        return App()

    gizmo = PluginSpec(name="gizmo", app_factory=factory)
    result = _run(["plugin", "schema", "gizmo.thing"], gizmo)
    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout)["required"] == ["size"]


def test_plugin_schema_loads_contract_offers_to_find_a_kind(tmp_path: Path) -> None:
    (tmp_path / "untaped_gadgets_records.py").write_text(
        "from untaped.contracts import Record\n\n\n"
        'class Gadget(Record, kind="gadgets.gadget"):\n'
        "    name: str\n"
    )

    def offers() -> tuple[Contract, ...]:
        import untaped_gadgets_records  # noqa: F401 - the lazy import under test

        return ()

    gadgets = PluginSpec(name="gadgets", provides={"shelf": offers})
    with pytest.MonkeyPatch.context() as patch:
        patch.syspath_prepend(str(tmp_path))
        result = _run(["plugin", "schema", "gadgets.gadget"], shelf_spec(), gadgets)
    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout)["required"] == ["name"]


def test_plugin_list_contracts_names_an_owner_whose_contracts_break() -> None:
    def broken() -> tuple[type[Contract], ...]:
        raise ImportError("acme api broke")

    result = _run(
        ["plugin", "list", "--contracts"], *_all(), PluginSpec(name="acme", contracts=broken)
    )
    assert "acme's contracts couldn't be read" in result.stderr


def test_plugin_schema_of_an_unknown_kind_exits_2() -> None:
    result = _run(["plugin", "schema", "nope.thing"], *_all())
    assert result.exit_code == 2
    assert "record kind not found: 'nope.thing'; known: none" in result.stderr
    result = _run(["plugin", "schema", "shelf.nope"], *_all())
    assert "known: shelf.book" in result.stderr


# --- doctor -----------------------------------------------------------------


def test_doctor_warns_about_rankings_it_cant_follow_and_names_the_fix() -> None:
    write_config(
        LIBRARY_CONFIG + "    shelf:\n      extensions:\n"
        "        gone: {rank: {x: [shop]}}\n"
        "        book_source: {rank: {nope: [shop], to_book: [library], "
        "books: [gitlab, shop, gitea]}}\n"
    )
    compose(*_all())
    rows = {(row.title, row.detail): row.fix for row in doctor_rows() if row.check == "rank"}
    assert rows == {
        (
            "rank-unknown-method",
            "shelf.extensions.gone.rank.x: shelf declares no contract gone; the ranking is ignored",
        ): "--profile default plugin rank shelf.gone x",
        (
            "rank-unknown-method",
            "shelf.extensions.book_source.rank.nope: shelf declares no method "
            "book_source.nope; the ranking is ignored",
        ): "--profile default plugin rank shelf.book_source nope",
        (
            "rank-unknown-method",
            "shelf.extensions.book_source.rank.to_book: shelf asks no one for "
            "book_source.to_book (a bridge); the ranking is ignored",
        ): "--profile default plugin rank shelf.book_source to_book",
        (
            "rank-not-installed",
            "shelf.extensions.book_source.rank.books ranks gitlab, gitea, which are not installed",
        ): "--profile default plugin rank shelf.book_source books shop",
    }


def test_doctor_rank_rows_carry_a_runnable_fix() -> None:
    write_config(
        "profiles:\n  default:\n    shelf:\n      extensions:\n        gone: {rank: {x: [shop]}}\n"
    )
    result = _run(["doctor", "-f", "json"], *_all())
    [row] = [row for row in json.loads(result.stdout) if row["check"] == "rank"]
    assert (row["plugin"], row["status"]) == ("shelf", "warn")
    assert row["fix"] == ["--profile", "default", "plugin", "rank", "shelf.gone", "x"]
    _run(row["fix"], *_all())
    assert _extensions() == {}
    result = _run(["doctor", "-f", "json"], *_all())
    assert not [row for row in json.loads(result.stdout) if row["check"] == "rank"]


def test_doctors_rank_fix_targets_the_profile_that_holds_the_ranking() -> None:
    """The ranking lives in ``default`` while ``work`` is active: the fix edits ``default``."""
    write_config(
        "active: work\nprofiles:\n  default:\n    shelf:\n      extensions:\n"
        "        gone: {rank: {x: [shop]}}\n"
        "        book_source: {rank: {books: [gitlab, shop]}}\n"
        "  work: {}\n"
    )
    result = _run(["doctor", "-f", "json"], *_all())
    fixes = sorted(row["fix"] for row in json.loads(result.stdout) if row["check"] == "rank")
    assert fixes == [
        ["--profile", "default", "plugin", "rank", "shelf.book_source", "books", "shop"],
        ["--profile", "default", "plugin", "rank", "shelf.gone", "x"],
    ]
    for fix in fixes:
        assert _run(fix, *_all()).exit_code == 0
    result = _run(["doctor", "-f", "json"], *_all())
    assert not [row for row in json.loads(result.stdout) if row["check"] == "rank"]
    result = _run(["--profile", "work", "config", "get", "shelf.extensions"], *_all())
    assert json.loads(result.stdout) == {"book_source": {"rank": {"books": ["shop"]}}}


def test_a_ranking_set_in_the_environment_gets_no_config_fix() -> None:
    compose(*_all())
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("UNTAPED_SHELF__EXTENSIONS__BOOK_SOURCE__RANK__BOOKS", '["gitlab","shop"]')
        [row] = [row for row in doctor_rows() if row.check == "rank"]
    assert row.fix is None
    assert row.detail.endswith("(set by UNTAPED_SHELF__EXTENSIONS__BOOK_SOURCE__RANK__BOOKS)")


def test_a_quarantined_plugin_stays_in_its_ranking() -> None:
    broken = PluginSpec(name="gitlab", settings=_Reserved)
    write_config(RANKED.replace("[shop, library]", "[gitlab, shop]"))
    compose(*_all(), broken)
    assert not [row for row in doctor_rows() if row.check == "rank"]


def test_doctor_limits_contract_rows_to_the_plugins_asked_for() -> None:
    write_config(LIBRARY_CONFIG)
    compose(*_all())
    assert {row.plugin for row in doctor_rows(frozenset({"shop"}))} == {"shop"}


def test_a_provider_whose_ready_raises_is_inactive_in_doctor() -> None:
    class Broken(Shop):
        def ready(self) -> None:
            raise RuntimeError("boom")

    compose(shelf_spec(), PluginSpec(name="shop", provides={"shelf": lambda: (Broken(),)}))
    [row] = doctor_rows()
    assert (row.status, row.title) == ("pass", "not-configured")
    assert row.detail == "shelf.book_source: inactive: ready() raised RuntimeError: boom"


def test_rank_rows_wait_for_a_broken_owner_section() -> None:
    write_config(RANKED.replace("[shop, library]", "[shop, shop]"))
    compose(*_all())
    assert not [row for row in doctor_rows() if row.check == "rank"]
    with pytest.raises(ConfigError, match="shop ranked twice"):
        gather(BookSource.books)()


def test_doctor_has_one_row_per_provider() -> None:
    write_config(LIBRARY_CONFIG)
    result = _run(["doctor", "-f", "json"], *_all())
    rows = [
        (row["plugin"], row["status"], row["title"])
        for row in json.loads(result.stdout)
        if row["check"] == "contract-provider"
    ]
    assert rows == [
        ("kiosk", "pass", "active"),
        ("library", "pass", "active"),
        ("shop", "pass", "active"),
    ]
