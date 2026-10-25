"""The index as a store: one file per layout at the default path, a set path used as it is."""

from __future__ import annotations

import ast
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from untaped.sdk import Filled, RefTarget, Store, code_version
from untaped.sdk.testing import assert_schema_pinned
from untaped_ansible import settings
from untaped_ansible.infrastructure import open_index
from untaped_ansible.infrastructure.sqlite_schema import ADDITIVE_SINCE, SCHEMA_SQL, SCHEMA_VERSION
from untaped_ansible.settings import INDEX, INDEX_STORE, AnsibleSettings, default_index_path

pytestmark = pytest.mark.usefixtures("home_run")

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


def test_a_key_filled_by_another_parser_is_outdated(monkeypatch: pytest.MonkeyPatch) -> None:
    def fill(_key: str, _progress: object) -> Filled:
        return Filled()

    INDEX.serve({"source:prod": "source prod"}, refresh=True, fill=fill, read=lambda: None)
    assert INDEX.freshness("source:prod").outdated is False

    other = code_version("untaped_ansible.domain.parser", extra=("an edited parser",))
    monkeypatch.setattr(INDEX, "version", lambda: other)
    assert INDEX.freshness("source:prod").outdated is True


def test_a_key_filled_by_another_analyser_is_outdated(monkeypatch: pytest.MonkeyPatch) -> None:
    def fill(_key: str, _progress: object) -> Filled:
        return Filled()

    INDEX.serve({"source:prod": "source prod"}, refresh=True, fill=fill, read=lambda: None)
    assert INDEX.freshness("source:prod").outdated is False
    monkeypatch.setattr(settings, "analyser_version", lambda: "an edited analyser")

    assert INDEX.freshness("source:prod").outdated is True


def test_the_default_index_is_its_layout_file_beside_the_setting() -> None:
    assert AnsibleSettings().index_path.expanduser() == _plugin() / "index.sqlite3"
    assert default_index_path() == _plugin() / "index.sqlite3"
    assert INDEX_STORE.path() == _plugin() / "index.v4.sqlite3"


def test_a_file_at_the_settings_default_is_never_the_index() -> None:
    plain = _plugin() / "index.sqlite3"
    _sqlite(plain, "someone's file")

    index = open_index(AnsibleSettings())

    layout = _plugin() / "index.v4.sqlite3"
    assert index._path == layout
    assert index.status("source:prod") is None  # builds its own layout
    assert _mark(plain) == "someone's file"
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

    index = open_index(AnsibleSettings(index_path=mine))
    index.status("source:prod")

    assert index._path == mine and mine.is_file()
    assert not Path(f"{mine}.inuse").exists()
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


# --- cleanup -------------------------------------------------------------------


def _fill_index() -> None:
    def fill(_key: str, _progress: object) -> Filled:
        return Filled()

    INDEX.serve({"source:prod": "source prod"}, refresh=True, fill=fill, read=lambda: None)


def test_a_registered_install_removes_the_layout_nothing_holds_after_a_fill() -> None:
    from untaped.sdk.storage import _refs as refs  # untaped: allow private-test-import

    refs.register([RefTarget(plugin="ansible", store="index", layout=4)])
    newer = _plugin() / "index.v5.sqlite3"
    _sqlite(newer, "a one-off's layout 5")
    (_plugin() / "cached" / "index.v5" / "default").mkdir(parents=True)

    open_index(AnsibleSettings())
    _fill_index()

    assert not newer.exists()
    assert not (_plugin() / "cached" / "index.v5").exists()
    assert (_plugin() / "cached" / "index.v4").is_dir()


def test_an_unregistered_install_keeps_both_layouts() -> None:
    newer = _plugin() / "index.v5.sqlite3"
    _sqlite(newer, "layout 5")
    open_index(AnsibleSettings())
    _fill_index()
    assert _mark(newer) == "layout 5"


def test_a_set_index_path_never_prunes_the_default_place(tmp_path: Path) -> None:
    from untaped.sdk.storage import _refs as refs  # untaped: allow private-test-import

    refs.register([RefTarget(plugin="ansible", store="index", layout=4)])
    newer = _plugin() / "index.v5.sqlite3"
    _sqlite(newer, "layout 5")
    open_index(AnsibleSettings(index_path=tmp_path / "idx.sqlite3"))
    _fill_index()
    assert _mark(newer) == "layout 5"


def test_a_layout_a_profile_points_index_path_at_is_never_pruned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Another profile's index stays, while a run on the default path fills and prunes."""
    from untaped.sdk.storage import _refs as refs  # untaped: allow private-test-import

    refs.register([RefTarget(plugin="ansible", store="index", layout=4)])
    pinned = _plugin() / "index.v3.sqlite3"
    _sqlite(pinned, "a profile's own index")
    monkeypatch.setenv("UNTAPED_ANSIBLE__INDEX_PATH", str(pinned))  # what the guard reads
    open_index(AnsibleSettings())  # this run's own settings: the default path
    _fill_index()
    assert _mark(pinned) == "a profile's own index"
