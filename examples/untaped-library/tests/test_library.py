"""The example provider with its owner, tested the way any third-party plugin would be."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from untaped_library.providers.shelf import LibraryBooks
from untaped_library.records import Volume

from untaped.sdk import StaleData
from untaped.testing import assert_fills, invoke_root
from untaped_lint.testing import assert_clean

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


def test_library_passes_lint() -> None:
    assert_clean("library")


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
def test_the_answer_is_cached_per_provider_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    path = Path(os.environ["UNTAPED_CONFIG"])
    path.write_text(
        _CONFIG
        + _CONFIG.removeprefix("profiles:\n  default:\n").join(["  same:\n", ""])
        + "  other:\n    library:\n      volumes: [{shelf_mark: C3, name: Kim, pages: 9}]\n",
        encoding="utf-8",
    )
    cached = Path(os.environ["HOME"]) / ".untaped" / "plugins" / "shelf" / "cached"
    assert invoke_root(["shelf", "list"]).exit_code == 0
    monkeypatch.setenv("UNTAPED_PROFILE", "same")
    assert invoke_root(["shelf", "list"]).exit_code == 0
    [entry] = cached.rglob("*.json")  # the same library settings share the answer
    assert entry.parent.parent.name == "book_source_books"
    monkeypatch.setenv("UNTAPED_PROFILE", "other")
    assert invoke_root(["shelf", "list"]).exit_code == 0
    assert len(list(cached.rglob("*.json"))) == 2


@pytest.mark.usefixtures("configured")
def test_a_failed_refresh_serves_the_cached_books_with_one_stale_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert invoke_root(["shelf", "list"]).exit_code == 0
    home = Path(os.environ["HOME"])
    [entry] = (home / ".untaped" / "plugins" / "shelf" / "cached").rglob("*.json")
    head, value = entry.read_text(encoding="utf-8").splitlines()  # the stamp, then the answer
    stored = json.loads(head)["untaped"]
    stored["refreshed_at"] = "2020-01-01T00:00:00Z"  # long expired: the next call is live
    entry.write_text(json.dumps({"untaped": stored}) + "\n" + value + "\n", encoding="utf-8")

    def down(*_args: object, **_kwargs: object) -> Volume:
        raise RuntimeError("the catalogue is down")

    monkeypatch.setattr(Volume, "model_validate", down)
    result = invoke_root(["shelf", "list", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert [row["title"] for row in json.loads(result.stdout)] == ["Dune", "Emma"]
    [stale] = result.diagnostics
    assert isinstance(stale, StaleData)
    assert (stale.plugin, stale.cache, stale.key) == (
        "library",
        "shelf.book_source_books",
        stored["key"],
    )
    assert stale.message.startswith("library: refresh failed (the catalogue is down)")
