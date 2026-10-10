"""The example provider with its owner, tested the way any third-party plugin would be."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from untaped_library.adapters.shelf import LibraryBooks
from untaped_library.records import Volume

from untaped.testing import assert_fills, check_conventions, invoke_root

_CONFIG = """\
profiles:
  default:
    library:
      volumes:
        - {shelf_mark: A1, name: Dune, pages: 412}
        - {shelf_mark: B2, name: Emma, pages: 474}
"""


@pytest.fixture
def configured() -> None:
    path = Path(os.environ["UNTAPED_CONFIG"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_CONFIG, encoding="utf-8")


def test_library_follows_the_conventions() -> None:
    check_conventions("library")


def test_the_library_fills_the_shelf_contract() -> None:
    assert_fills(
        LibraryBooks,
        samples=[
            Volume(shelf_mark="A1", name="Dune", pages=412),
            {"shelf_mark": "B2", "name": "Emma"},
        ],
    )


def test_unconfigured_the_owner_has_no_ready_provider() -> None:
    result = invoke_root(["shelf", "find", "Dune"])
    assert result.exit_code == 4
    assert "library: no volumes configured (set library.volumes)" in result.output


@pytest.mark.usefixtures("configured")
def test_the_owner_lists_the_library_books_with_their_source() -> None:
    result = invoke_root(["shelf", "list", "--format", "json"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert [row["title"] for row in rows] == ["Dune", "Emma"]
    assert rows[0]["source"] == {
        "plugin": "library",
        "kind": "library.volume",
        "record": {"shelf_mark": "A1", "name": "Dune", "pages": 412},
    }


@pytest.mark.usefixtures("configured")
def test_the_owner_finds_one_book_and_reports_none() -> None:
    found = invoke_root(["shelf", "find", "Emma", "--format", "json"])
    assert found.exit_code == 0, found.output
    assert json.loads(found.stdout)["pages"] == 474
    missing = invoke_root(["shelf", "find", "Ulysses"])
    assert missing.exit_code == 2
    assert "no provider of shelf.book_source matched" in missing.output


@pytest.mark.usefixtures("configured")
def test_the_answer_is_cached_per_profile() -> None:
    assert invoke_root(["shelf", "list"]).exit_code == 0
    home = Path(os.environ["HOME"])
    [entry] = (home / ".untaped" / "plugins" / "library" / "cache").rglob("*.json")
    assert entry.parent.name == "default"
    assert entry.parent.parent.name == "shelf.book_source.books"
