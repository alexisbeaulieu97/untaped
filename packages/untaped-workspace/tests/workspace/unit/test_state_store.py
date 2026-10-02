"""StateWorkspaceStore against an isolated state.yml."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from untaped.sdk import StateCollection
from untaped_workspace.domain import RepoSpec, WorkspaceRecord
from untaped_workspace.errors import WorkspaceError, WorkspaceNotFoundError
from untaped_workspace.infrastructure import StateWorkspaceStore

T0 = datetime(2026, 10, 1, tzinfo=UTC)
SPEC = RepoSpec(url="u", name="acme/api", dir="api", branch="b", base="main")


def test_create_get_add_archive() -> None:
    store = StateWorkspaceStore()
    store.create(WorkspaceRecord(name="w", created_at=T0))
    store.add_repos("w", [SPEC])
    assert store.get("w") == WorkspaceRecord(name="w", created_at=T0, repos=(SPEC,))
    archived = store.archive("w", at=T0)
    assert archived.archived_at == T0
    assert store.get("w") is None
    assert [a.name for a in store.archived()] == ["w"]


def test_create_twice_is_a_conflict() -> None:
    store = StateWorkspaceStore()
    store.create(WorkspaceRecord(name="w", created_at=T0))
    with pytest.raises(WorkspaceError) as caught:
        store.create(WorkspaceRecord(name="w", created_at=T0))
    assert caught.value.category == "conflict"
    assert caught.value.hint == "run `untaped workspace add w --repo REPO`"


def test_a_name_can_be_archived_twice() -> None:
    store = StateWorkspaceStore()
    for _ in range(2):
        store.create(WorkspaceRecord(name="w", created_at=T0))
        store.archive("w", at=T0)
    assert len(store.archived()) == 2


def test_add_to_unknown_workspace() -> None:
    with pytest.raises(WorkspaceNotFoundError):
        StateWorkspaceStore().add_repos("nope", [SPEC])


def test_add_repos_twice_records_once() -> None:
    store = StateWorkspaceStore()
    store.create(WorkspaceRecord(name="w", created_at=T0))
    store.add_repos("w", [SPEC])
    record = store.add_repos("w", [SPEC])
    assert record.repos == (SPEC,)
    stored = store.get("w")
    assert stored is not None and stored.repos == (SPEC,)


def test_one_repo_under_two_url_forms_records_once() -> None:
    store = StateWorkspaceStore()
    store.create(WorkspaceRecord(name="w", created_at=T0))
    https = SPEC.model_copy(update={"url": "https://github.com/acme/api.git"})
    ssh = SPEC.model_copy(update={"url": "git@github.com:acme/api.git", "dir": "acme-api"})
    store.add_repos("w", [https])
    assert store.add_repos("w", [ssh]).repos == (https,)


def test_a_held_workspace_lock_makes_others_wait_then_fail(tmp_path: Path) -> None:
    holder = StateWorkspaceStore(workspaces_dir=tmp_path)
    other = StateWorkspaceStore(workspaces_dir=tmp_path, lock_timeout=0.1)
    with holder.locked("w"):
        with pytest.raises(WorkspaceError) as caught, other.locked("w"):
            pass
        with other.locked("v"):  # another workspace is not held
            pass
    assert (caught.value.category, caught.value.system) == ("unavailable", "local")
    assert str(caught.value) == "workspace w is busy (another untaped process)"
    assert caught.value.hint
    with other.locked("w"):  # free again
        pass


def test_archive_keeps_the_history_record_when_removing_the_active_one_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = StateWorkspaceStore()
    store.create(WorkspaceRecord(name="w", created_at=T0))
    store.add_repos("w", [SPEC])

    def fail(self: StateCollection, ident: str) -> bool:
        raise OSError("disk full")

    monkeypatch.setattr(StateCollection, "remove", fail)
    with pytest.raises(OSError):
        store.archive("w", at=T0)
    [archived] = store.archived()
    assert (archived.name, archived.repos) == ("w", (SPEC,))
    assert store.get("w") is not None  # a recoverable duplicate, not a loss
