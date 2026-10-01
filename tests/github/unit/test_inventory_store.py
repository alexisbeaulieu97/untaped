"""JSON inventory file: round trip, corruption and locking."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from untaped.capabilities.github.application.inventory import RepositoryInventoryItem
from untaped.capabilities.github.application.inventory_cache import RepoInventory
from untaped.capabilities.github.infrastructure.inventory_store import JsonInventoryStore

INVENTORY = RepoInventory(
    repos=(
        RepositoryInventoryItem(
            full_name="acme/api",
            clone_url="https://github.com/acme/api.git",
            default_branch="main",
            description="Core REST API",
            archived=False,
            pushed_at="2026-09-30T10:00:00Z",
        ),
    ),
    refreshed_at=datetime(2026, 10, 1, 12, 0, tzinfo=UTC),
    scope_key="https://api.github.com|orgs=acme|teams=",
)


def test_round_trip(tmp_path: Path) -> None:
    store = JsonInventoryStore(tmp_path / "inv.json")
    store.save(INVENTORY)
    assert store.load() == INVENTORY


def test_missing_file_loads_as_none(tmp_path: Path) -> None:
    assert JsonInventoryStore(tmp_path / "absent.json").load() is None


def test_corrupt_file_loads_as_none(tmp_path: Path) -> None:
    path = tmp_path / "inv.json"
    path.write_text("{not json")
    assert JsonInventoryStore(path).load() is None


def test_unknown_version_loads_as_none(tmp_path: Path) -> None:
    path = tmp_path / "inv.json"
    path.write_text('{"version": 99}')
    assert JsonInventoryStore(path).load() is None


def test_save_creates_parent_directories(tmp_path: Path) -> None:
    store = JsonInventoryStore(tmp_path / "deep" / "inv.json")
    store.save(INVENTORY)
    assert store.load() == INVENTORY


def test_lock_is_reentrant_across_instances_in_sequence(tmp_path: Path) -> None:
    path = tmp_path / "inv.json"
    with JsonInventoryStore(path).lock():
        pass
    with JsonInventoryStore(path).lock():
        pass


def test_naive_timestamp_loads_as_none(tmp_path: Path) -> None:
    path = tmp_path / "inv.json"
    path.write_text(
        '{"version": 1, "scope_key": "s", "refreshed_at": "2026-10-01T12:00:00", "repos": []}'
    )
    assert JsonInventoryStore(path).load() is None
