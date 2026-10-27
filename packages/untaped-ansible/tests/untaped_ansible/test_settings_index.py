"""The index's place and its source data: one file per layout in ``ansible.index_dir``."""

from __future__ import annotations

import ast
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from ansible_index_support import index_path
from ansible_test_support import Sections, running
from untaped.sdk import Filled
from untaped.sdk.testing import Home, assert_schema_pinned
from untaped_ansible import settings
from untaped_ansible.infrastructure import open_index
from untaped_ansible.infrastructure.sqlite_schema import ADDITIVE_SINCE, SCHEMA_SQL, SCHEMA_VERSION
from untaped_ansible.settings import INDEX_DIR, SOURCE_DATA

_PINS = Path(__file__).parent / "infrastructure" / "index_schema"
_SOURCE = Path(__file__).parents[2] / "src" / "untaped_ansible"


def _sqlite(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db:
        db.execute("create table mark (value text)")
        db.execute("insert into mark values (?)", (value,))
        db.commit()


def _mark(path: Path) -> str:
    with closing(sqlite3.connect(path)) as db:
        return str(db.execute("select value from mark").fetchone()[0])


def _fill() -> None:
    def fill(_key: str, _progress: object) -> Filled:
        return Filled()

    SOURCE_DATA.serve({"source:prod": "source prod"}, refresh=True, fill=fill, read=lambda: None)


def test_the_index_schema_is_pinned() -> None:
    assert_schema_pinned(
        SCHEMA_SQL, version=SCHEMA_VERSION, additive_since=ADDITIVE_SINCE, pins=_PINS
    )


def test_the_layout_is_the_oldest_schema_this_reader_opens() -> None:
    assert INDEX_DIR.layout == ADDITIVE_SINCE
    assert SOURCE_DATA.follows is INDEX_DIR


def test_a_key_filled_by_another_parser_or_analyser_is_outdated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with running():
        _fill()
        assert SOURCE_DATA.freshness("source:prod").outdated is False
        monkeypatch.setattr(settings, "parser_version", lambda: "an edited parser")
        assert SOURCE_DATA.freshness("source:prod").outdated is True
        monkeypatch.undo()
        assert SOURCE_DATA.freshness("source:prod").outdated is False
        monkeypatch.setattr(settings, "analyser_version", lambda: "an edited analyser")
        assert SOURCE_DATA.freshness("source:prod").outdated is True


def test_the_default_index_is_its_layout_file_in_the_plugin_folder(
    sections: Sections, home: Home
) -> None:
    del sections.ansible["index_dir"]

    with running():
        index = open_index()

    assert index._path == home.plugin("ansible") / "index" / "index.v4.sqlite3"


def test_a_set_folder_holds_the_layout_file_and_never_opens_another(
    tmp_path: Path,
) -> None:
    plain = tmp_path / "index.sqlite3"
    _sqlite(plain, "someone's file")
    _sqlite(tmp_path / "index.v5.sqlite3", "layout 5")

    with running():
        index = open_index()
        assert index.status("source:prod") is None  # builds its own layout

    assert index._path == index_path(tmp_path)
    assert _mark(plain) == "someone's file"
    assert _mark(tmp_path / "index.v5.sqlite3") == "layout 5"


def test_the_source_data_takes_the_index_s_layout(home: Home) -> None:
    with running():
        open_index()
        _fill()

    assert (home.plugin("ansible") / "cached" / "source_data.v4").is_dir()


def test_open_index_is_the_only_way_the_plugin_opens_the_index() -> None:
    """Every opener goes through the index's place; a direct construction would bypass it."""
    callers = []
    for path in sorted(_SOURCE.rglob("*.py")):
        if path.name == "sqlite_index.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        callers += [
            f"{path.relative_to(_SOURCE)}:{node.lineno}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "SqliteDependencyIndex"
        ]
    assert callers == [], "open the index with open_index(), not SqliteDependencyIndex()"
