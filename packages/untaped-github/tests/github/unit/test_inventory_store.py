"""JSON inventory file: round trip, corruption and locking."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from untaped.sdk import UntapedError
from untaped_github.application.inventory import RepositoryInventoryItem
from untaped_github.domain.inventory import RepoInventory
from untaped_github.infrastructure.inventory_store import JsonInventoryStore

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


def test_lock_can_be_taken_again_after_release(tmp_path: Path) -> None:
    path = tmp_path / "inv.json"
    with JsonInventoryStore(path).lock():
        assert (tmp_path / "inv.json.lock").exists()
    with JsonInventoryStore(path).lock():
        pass


_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


@pytest.mark.skipif(_ROOT, reason="root ignores directory permissions")
def test_save_into_a_read_only_directory_is_an_attributed_error(tmp_path: Path) -> None:
    parent = tmp_path / "ro"
    parent.mkdir()
    parent.chmod(0o500)
    try:
        with pytest.raises(UntapedError, match="could not write the repository inventory") as e:
            JsonInventoryStore(parent / "inv.json").save(INVENTORY)
    finally:
        parent.chmod(0o700)
    assert e.value.category == "failed"
    assert e.value.system == "local"


@pytest.mark.skipif(_ROOT, reason="root ignores directory permissions")
def test_lock_under_a_read_only_directory_is_an_attributed_error(tmp_path: Path) -> None:
    parent = tmp_path / "ro"
    parent.mkdir()
    parent.chmod(0o500)
    try:
        with (
            pytest.raises(UntapedError, match="could not write the repository inventory") as e,
            JsonInventoryStore(parent / "deep" / "inv.json").lock(),
        ):
            pass
    finally:
        parent.chmod(0o700)
    assert e.value.category == "failed"
    assert e.value.system == "local"


def test_a_symlink_alias_and_its_target_share_one_lock_file(tmp_path: Path) -> None:
    target = tmp_path / "real.json"
    alias = tmp_path / "alias.json"
    alias.symlink_to(target)
    with JsonInventoryStore(alias).lock():
        assert (tmp_path / "real.json.lock").exists()
    assert not (tmp_path / "alias.json.lock").exists()


def test_a_symlink_alias_writes_through_to_its_target(tmp_path: Path) -> None:
    target = tmp_path / "real.json"
    alias = tmp_path / "alias.json"
    alias.symlink_to(target)  # dangling until the first save
    JsonInventoryStore(alias).save(INVENTORY)
    assert JsonInventoryStore(target).load() == INVENTORY


@pytest.mark.skipif(_ROOT, reason="root ignores file permissions")
def test_an_unopenable_lock_file_is_attributed_locally(tmp_path: Path) -> None:
    path = tmp_path / "inv.json"
    lock_file = tmp_path / "inv.json.lock"
    lock_file.write_text("")
    lock_file.chmod(0o000)
    try:
        with (
            pytest.raises(UntapedError, match="could not lock the repository inventory") as e,
            JsonInventoryStore(path).lock(),
        ):
            pass
    finally:
        lock_file.chmod(0o600)
    assert e.value.system == "local"


def test_naive_timestamp_loads_as_none(tmp_path: Path) -> None:
    path = tmp_path / "inv.json"
    path.write_text(
        '{"version": 1, "scope_key": "s", "refreshed_at": "2026-10-01T12:00:00", "repos": []}'
    )
    assert JsonInventoryStore(path).load() is None
