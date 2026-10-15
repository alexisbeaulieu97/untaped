"""ansible's ``setup migrate-dirs`` rows delete its old caches, never the repo store."""

from __future__ import annotations

from pathlib import Path

import pytest

from untaped.sdk import DirMigration, MigrationOptions, PluginContext
from untaped.settings import get_settings
from untaped_ansible import SPEC
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


def test_the_10x_index_moves_with_its_journal_while_the_setting_keeps_its_default() -> None:
    old = Path.home() / ".untaped" / "ansible-index.sqlite3"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"index")
    Path(f"{old}-journal").write_bytes(b"journal")
    new = Path.home() / ".untaped" / "plugins" / "ansible" / "index.sqlite3"
    ctx, row = PluginContext(settings=AnsibleSettings()), _index_row()

    assert [(r.action, r.source, r.destination) for r in row.preview(ctx, OPTIONS)] == [
        ("move", f"{old}-journal", f"{new}-journal"),
        ("move", str(old), str(new)),
    ]
    assert [o.action for o in row.apply(ctx, OPTIONS)] == ["moved"]
    assert new.read_bytes() == b"index" and Path(f"{new}-journal").read_bytes() == b"journal"
    assert not old.exists() and AnsibleSettings().index_path.expanduser() == new


@pytest.mark.parametrize("settings", [None, AnsibleSettings(index_path=Path("~/elsewhere.db"))])
def test_a_profile_naming_another_index_or_unreadable_settings_move_nothing(
    settings: AnsibleSettings | None,
) -> None:
    old = Path.home() / ".untaped" / "ansible-index.sqlite3"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"index")

    assert _index_row().preview(PluginContext(settings=settings), OPTIONS) == []
    assert old.is_file()
