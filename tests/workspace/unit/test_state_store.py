"""StateWorkspaceStore against an isolated state.yml."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from untaped.capabilities.workspace.domain import RepoSpec, WorkspaceRecord
from untaped.capabilities.workspace.errors import WorkspaceError, WorkspaceNotFoundError
from untaped.capabilities.workspace.infrastructure import StateWorkspaceStore

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
