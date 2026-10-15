"""ansible's ``setup migrate-dirs`` rows delete its old caches, never the repo store."""

from __future__ import annotations

from pathlib import Path

import pytest

from ansible.unit.test_sqlite_index import _commit, _scan
from untaped.sdk import DirMigration, MigrationOptions, PluginContext
from untaped.settings import get_settings
from untaped_ansible import SPEC
from untaped_ansible.infrastructure.sqlite_index import SqliteDependencyIndex
from untaped_ansible.settings import AnsibleSettings

CTX, OPTIONS = PluginContext(settings=None), MigrationOptions()


def _rows() -> list[tuple[str, str]]:
    return [
        (row.action, row.source)
        for migration in SPEC.migrations
        for row in migration.preview(CTX, OPTIONS)
    ]


def test_the_old_caches_are_deleted() -> None:
    old = Path.home() / ".untaped" / "ansible-cache" / "x"
    old.mkdir(parents=True)

    assert _rows() == [("delete", str(old.parent))]
    for migration in SPEC.migrations:
        migration.apply(CTX, OPTIONS)
    assert not old.parent.exists()


def test_a_root_configured_to_the_store_stays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = tmp_path / "store"
    (store / "host" / "repo.git").mkdir(parents=True)
    monkeypatch.setenv("UNTAPED_GIT__STORE_DIR", str(store))
    monkeypatch.setenv("UNTAPED_ANSIBLE__CACHE_DIR", str(store))
    get_settings.cache_clear()

    assert _rows() == [("keep", str(store))]
    for migration in SPEC.migrations:
        migration.apply(CTX, OPTIONS)
    assert (store / "host" / "repo.git").is_dir()


def _index_row() -> DirMigration:
    return next(m for m in SPEC.migrations if m.id == "ansible.index")


def _old_index() -> Path:
    old = Path.home() / ".untaped" / "ansible-index.sqlite3"
    old.parent.mkdir(parents=True, exist_ok=True)
    _commit(SqliteDependencyIndex(old), scans=(_scan(),))
    return old


def _new() -> Path:
    return Path.home() / ".untaped" / "plugins" / "ansible" / "index.sqlite3"


def test_the_10x_index_moves_with_its_journal() -> None:
    old = _old_index()
    Path(f"{old}-journal").write_bytes(b"journal")
    row = _index_row()

    assert [(r.action, r.source, r.destination) for r in row.preview(CTX, OPTIONS)] == [
        ("move", str(old), str(_new()))
    ]
    assert [o.action for o in row.apply(CTX, OPTIONS)] == ["moved"]
    assert Path(f"{_new()}-journal").read_bytes() == b"journal" and not old.exists()
    assert AnsibleSettings().index_path.expanduser() == _new()
    assert SqliteDependencyIndex(_new()).status("source:prod") is not None


def test_an_index_a_command_opened_first_is_replaced() -> None:
    old = _old_index()
    SqliteDependencyIndex(_new()).status("source:prod")  # an 11.0 read creates an empty index
    row = _index_row()

    (move,) = row.preview(CTX, OPTIONS)
    assert move.action == "move" and "a command made before the move" in move.detail
    assert [o.action for o in row.apply(CTX, OPTIONS)] == ["moved"]
    assert not old.exists() and SqliteDependencyIndex(_new()).status("source:prod") is not None


def test_an_index_with_refreshed_data_is_never_replaced() -> None:
    old = _old_index()
    _commit(SqliteDependencyIndex(_new()), scans=(_scan(source_ref="develop"),))
    row = _index_row()

    (keep,) = row.preview(CTX, OPTIONS)
    assert keep.action == "keep" and "already exists" in keep.detail
    assert [o.action for o in row.apply(CTX, OPTIONS)] == ["unchanged"]
    assert old.is_file()


def test_a_profile_still_naming_the_old_index_keeps_it(monkeypatch: pytest.MonkeyPatch) -> None:
    old = _old_index()
    monkeypatch.setenv("UNTAPED_ANSIBLE__INDEX_PATH", str(old))

    (keep,) = _index_row().preview(CTX, OPTIONS)
    assert keep.action == "keep" and "ansible.index_path" in keep.detail
