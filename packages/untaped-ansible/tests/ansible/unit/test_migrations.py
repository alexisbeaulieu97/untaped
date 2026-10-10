"""ansible's ``setup migrate-dirs`` rows delete its old caches, never the repo store."""

from __future__ import annotations

from pathlib import Path

import pytest

from untaped.sdk import MigrationOptions, PluginContext
from untaped.settings import get_settings
from untaped_ansible import SPEC

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
