"""The index as a store: one file per layout at the default path, a set path used as it is."""

from __future__ import annotations

import ast
import sqlite3
from contextlib import closing
from pathlib import Path

from untaped.sdk import Store, code_version
from untaped.testing import assert_schema_pinned
from untaped_ansible.infrastructure import open_index
from untaped_ansible.infrastructure.sqlite_schema import ADDITIVE_SINCE, SCHEMA_SQL, SCHEMA_VERSION
from untaped_ansible.settings import INDEX, INDEX_STORE, AnsibleSettings, default_index_path

_PINS = Path(__file__).parent / "index_schema"
_SOURCE = Path(__file__).parents[3] / "src" / "untaped_ansible"


def _plugin() -> Path:
    return Path.home() / ".untaped" / "plugins" / "ansible"


def _sqlite(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db:
        db.execute("create table mark (value text)")
        db.execute("insert into mark values (?)", (value,))
        db.commit()


def _mark(path: Path) -> str:
    with closing(sqlite3.connect(path)) as db:
        return str(db.execute("select value from mark").fetchone()[0])


def test_the_index_schema_is_pinned() -> None:
    assert_schema_pinned(
        SCHEMA_SQL, version=SCHEMA_VERSION, additive_since=ADDITIVE_SINCE, pins=_PINS
    )


def test_the_layout_is_the_oldest_schema_this_reader_opens() -> None:
    assert INDEX_STORE.layout == ADDITIVE_SINCE
    assert INDEX.store is INDEX_STORE


def test_the_index_is_refilled_when_its_parser_changes() -> None:
    assert INDEX.version is not None
    expected = code_version("untaped_ansible.domain.parser", distributions=("untaped-ansible",))
    assert INDEX.version() == expected


def test_the_default_index_is_its_layout_file_beside_the_setting() -> None:
    assert AnsibleSettings().index_path.expanduser() == _plugin() / "index.sqlite3"
    assert default_index_path() == _plugin() / "index.sqlite3"
    assert INDEX_STORE.path() == _plugin() / "index.v4.sqlite3"


def test_the_first_open_adopts_the_11_0_index_with_its_journal_and_sidecars() -> None:
    bare = _plugin() / "index.sqlite3"
    _sqlite(bare, "11.0")
    Path(f"{bare}-journal").write_bytes(b"journal")
    sidecars = _plugin() / "caches" / "index" / "default"
    sidecars.mkdir(parents=True)
    (sidecars / "source_prod.json").write_text("{}", encoding="utf-8")

    index = open_index(AnsibleSettings())
    INDEX.freshness("source:prod")  # the cache's first use moves its sidecars

    layout = _plugin() / "index.v4.sqlite3"
    assert index._path == layout
    assert Path(f"{layout}-journal").read_bytes() == b"journal"
    Path(f"{layout}-journal").unlink()  # not a real journal: SQLite would read it
    assert _mark(layout) == "11.0"
    assert not bare.exists() and not Path(f"{bare}-journal").exists()
    assert (_plugin() / "caches" / "index.v4" / "default" / "source_prod.json").is_file()
    assert not (_plugin() / "caches" / "index").exists()
    assert (_plugin() / "index.v4.sqlite3.inuse").exists()


def test_a_newer_layout_beside_it_is_never_opened() -> None:
    _sqlite(_plugin() / "index.v5.sqlite3", "layout 5")

    index = open_index(AnsibleSettings())
    assert index.status("source:prod") is None  # builds its own layout

    assert _mark(_plugin() / "index.v5.sqlite3") == "layout 5"
    assert Store("ansible", "index", base=default_index_path, layout=5).other_paths() == [
        (4, _plugin() / "index.v4.sqlite3")
    ]


def test_a_path_the_user_set_is_used_as_it_is(tmp_path: Path) -> None:
    mine = tmp_path / "idx.sqlite3"
    bare = _plugin() / "index.sqlite3"
    _sqlite(bare, "11.0")

    index = open_index(AnsibleSettings(index_path=mine))
    index.status("source:prod")

    assert index._path == mine and mine.is_file()
    assert not Path(f"{mine}.inuse").exists()
    assert _mark(bare) == "11.0"  # the default's bare file is not this run's business
    assert not (_plugin() / "index.v4.sqlite3").exists()


def test_open_index_is_the_only_way_the_plugin_opens_the_index() -> None:
    """Every opener goes through the store; a new direct construction would bypass it."""
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
    assert callers == [], "open the index with open_index(settings), not SqliteDependencyIndex()"
