"""ProjectWorkspaceStore: workspaces as rows in their projects, found by name or folder."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from untaped.sdk import UsageError
from untaped.sdk.testing import make_project
from untaped_workspace.domain.models import RepoSpec
from untaped_workspace.errors import WorkspaceError, WorkspaceNotFoundError
from untaped_workspace.infrastructure import ProjectWorkspaceStore

T0 = datetime(2026, 10, 1, tzinfo=UTC)
SPEC = RepoSpec(
    name="acme/api", url="https://git.example/acme/api.git", dir="api", branch="w", base="main"
)


def test_start_makes_the_folder_a_project_and_a_workspace(tmp_path: Path) -> None:
    store = ProjectWorkspaceStore()
    record = store.start(tmp_path / "ws" / "w", "w", at=T0)
    assert (tmp_path / "ws" / "w" / ".untaped" / "project.json").is_file()
    assert record.meta is not None and record.meta.created_by == "workspace"
    assert [each.name for each in store.all()] == ["w"]


def test_start_where_a_workspace_is_already_is_a_conflict(tmp_path: Path) -> None:
    store = ProjectWorkspaceStore()
    store.start(tmp_path / "w", "w", at=T0)
    with pytest.raises(WorkspaceError, match="already exists") as refused:
        store.start(tmp_path / "w", "w", at=T0)
    assert refused.value.category == "conflict"


def test_add_in_any_project_adopts_it_for_the_user(tmp_path: Path) -> None:
    store = ProjectWorkspaceStore()
    project = make_project(tmp_path / "PROJ-1")
    record = store.at(project.root)
    assert record is not None and record.meta is None
    adopted = store.adopt(record, at=T0)
    assert adopted.meta is not None and adopted.meta.created_by == "user"
    assert store.find("PROJ-1").root == project.root


def test_rows_are_kept_by_folder(tmp_path: Path) -> None:
    store = ProjectWorkspaceStore()
    record = store.start(tmp_path / "w", "w", at=T0)
    store.put_repos(record, [SPEC])
    again = store.put_repos(record, [SPEC.model_copy(update={"branch": "other"})])
    assert [(spec.dir, spec.branch) for spec in again.repos] == [("api", "other")]
    store.forget(again)
    left = store.at(record.root)
    assert left is not None and left.meta is None and not left.repos


def test_find_by_name_by_folder_and_from_inside(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ProjectWorkspaceStore()
    root = store.start(tmp_path / "w", "w", at=T0).root
    assert store.find("w").root == root
    assert store.find(str(root)).root == root
    (root / "api" / "src").mkdir(parents=True)
    monkeypatch.chdir(root / "api" / "src")
    assert store.current().root == root


def test_an_unknown_name_lists_the_known_ones(tmp_path: Path) -> None:
    store = ProjectWorkspaceStore()
    store.start(tmp_path / "w", "w", at=T0)
    with pytest.raises(WorkspaceNotFoundError, match="known: w"):
        store.find("nope")
    with pytest.raises(WorkspaceNotFoundError):
        store.named("nope")


def test_a_folder_in_no_project_is_usage(tmp_path: Path) -> None:
    with pytest.raises(UsageError, match="is in no project"):
        ProjectWorkspaceStore().find(str(tmp_path))


def test_two_active_workspaces_of_one_name_are_ambiguous(tmp_path: Path) -> None:
    store = ProjectWorkspaceStore()
    first = store.start(tmp_path / "a" / "w", "w", at=T0).root
    second = store.start(tmp_path / "b" / "w", "w", at=T0).root
    with pytest.raises(UsageError, match="ambiguous") as refused:
        store.find("w")
    assert str(first) in str(refused.value) and str(second) in str(refused.value)
    with pytest.raises(UsageError, match="ambiguous"):
        store.named("w")  # remove refuses too, rather than removing both
    assert store.find(str(second)).root == second  # a folder names one


def test_the_active_one_wins_then_the_newest_archived(tmp_path: Path) -> None:
    store = ProjectWorkspaceStore()
    old = store.start(tmp_path / "a" / "w", "w", at=T0)
    new = store.start(tmp_path / "b" / "w", "w", at=T0)
    store.set_archived(old, T0)
    assert store.find("w").root == new.root
    store.set_archived(new, T0 + timedelta(days=1))
    assert store.find("w").root == new.root
    resumed = store.set_archived(old, None)
    assert not resumed.archived
    assert store.find("w").root == old.root


def test_locked_yields_the_record_as_stored_now(tmp_path: Path) -> None:
    store = ProjectWorkspaceStore()
    record = store.start(tmp_path / "w", "w", at=T0)
    store.put_repos(record, [SPEC])
    with store.locked(record) as fresh:
        assert [spec.dir for spec in fresh.repos] == ["api"]
