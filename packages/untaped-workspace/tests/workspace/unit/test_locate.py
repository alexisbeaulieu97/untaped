"""Which workspace a command acts on."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from untaped_workspace.application.locate import locate_workspace
from untaped_workspace.domain import WorkspaceRecord
from untaped_workspace.errors import WorkspaceNotFoundError
from untaped_workspace.infrastructure import StateWorkspaceStore


@pytest.fixture
def store() -> StateWorkspaceStore:
    store = StateWorkspaceStore()
    store.create(WorkspaceRecord(name="w", created_at=datetime(2026, 10, 1, tzinfo=UTC)))
    return store


def test_by_name(store: StateWorkspaceStore, tmp_path: Path) -> None:
    assert locate_workspace(store, name="w", workspaces_dir=tmp_path, cwd=tmp_path).name == "w"


def test_unknown_name_lists_known(store: StateWorkspaceStore, tmp_path: Path) -> None:
    with pytest.raises(WorkspaceNotFoundError, match="known: w"):
        locate_workspace(store, name="x", workspaces_dir=tmp_path, cwd=tmp_path)


def test_from_a_subdirectory(store: StateWorkspaceStore, tmp_path: Path) -> None:
    deep = tmp_path / "w" / "api" / "src"
    deep.mkdir(parents=True)
    assert locate_workspace(store, name=None, workspaces_dir=tmp_path, cwd=deep).name == "w"


def test_outside_any_workspace(store: StateWorkspaceStore, tmp_path: Path) -> None:
    with pytest.raises(WorkspaceNotFoundError) as caught:
        locate_workspace(store, name=None, workspaces_dir=tmp_path / "ws", cwd=tmp_path)
    assert caught.value.hint
    assert caught.value.category == "usage"


def test_cwd_equal_to_workspaces_dir(store: StateWorkspaceStore, tmp_path: Path) -> None:
    with pytest.raises(WorkspaceNotFoundError) as caught:
        locate_workspace(store, name=None, workspaces_dir=tmp_path, cwd=tmp_path)
    assert caught.value.hint


def test_through_a_symlink(store: StateWorkspaceStore, tmp_path: Path) -> None:
    deep = tmp_path / "ws" / "w" / "api"
    deep.mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(deep)
    assert locate_workspace(store, name=None, workspaces_dir=tmp_path / "ws", cwd=link).name == "w"


def test_first_component_not_an_active_record(store: StateWorkspaceStore, tmp_path: Path) -> None:
    other = tmp_path / "other" / "api"
    other.mkdir(parents=True)
    with pytest.raises(WorkspaceNotFoundError):
        locate_workspace(store, name=None, workspaces_dir=tmp_path, cwd=other)
