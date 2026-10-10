"""S24: a host that refuses blob fetches by object id fails before anything is read or written."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from git.conftest import StoreFor, git
from untaped.sdk import ErrorCategory
from untaped.testing.git import GitRemote, global_config
from untaped_git.errors import StoreError
from untaped_git.infrastructure.store import RepoStore

MAIN = "refs/untaped/github/heads/main"


@pytest.fixture
def fetched(remote: GitRemote, store_for: StoreFor) -> RepoStore:
    remote.commit("src/a.py", "needle\n")
    store = store_for("github")
    store.fetch(branches=["main"])
    return store


def test_v0_refusal_is_unavailable(
    remote: GitRemote, fetched: RepoStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote.refuse_by_oid()
    real = RepoStore._missing
    listings: list[int] = []

    def missing(self: RepoStore, revisions: Sequence[str], paths: Sequence[str] = ()) -> list[str]:
        listings.append(1)
        return real(self, revisions, paths)

    monkeypatch.setattr(RepoStore, "_missing", missing)

    with pytest.raises(StoreError) as caught:
        fetched.prefetched(trees=[MAIN], paths=["src"])

    assert len(listings) == 1  # the refusal stops it before the guard's second listing

    assert caught.value.category == ErrorCategory.UNAVAILABLE
    assert caught.value.exit_code == 5
    assert "git.example refused to send blobs by object id" in str(caught.value)
    assert "--refetch" in (caught.value.hint or "")


def test_a_planted_leftover_is_unavailable(
    fetched: RepoStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = RepoStore._missing
    calls: list[int] = []

    def missing(self: RepoStore, revisions: Sequence[str], paths: Sequence[str] = ()) -> list[str]:
        found = real(self, revisions, paths)
        calls.append(len(found))
        return found if len(calls) == 1 else ["0" * 40]

    monkeypatch.setattr(RepoStore, "_missing", missing)

    with pytest.raises(StoreError) as caught:
        fetched.prefetched(trees=[MAIN], paths=["src"])

    assert caught.value.category == ErrorCategory.UNAVAILABLE
    assert "still missing" in str(caught.value)


def test_worktree_add_writes_nothing(remote: GitRemote, fetched: RepoStore, tmp_path: Path) -> None:
    remote.refuse_by_oid()
    before = git(fetched.path, "worktree", "list", "--porcelain")

    with pytest.raises(StoreError):
        fetched.worktree_add(tmp_path / "tree", MAIN)

    assert not (tmp_path / "tree").exists()
    assert git(fetched.path, "worktree", "list", "--porcelain") == before
    assert "worktreeconfig" not in git(fetched.path, "config", "--list").lower()


def test_v2_serves_the_blob(remote: GitRemote, fetched: RepoStore) -> None:
    remote.refuse_by_oid()
    global_config("protocol.version", "2")

    fetched.prefetched(trees=[MAIN], paths=["src"])
