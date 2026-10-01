"""Status and archive use cases with real git."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from untaped.capabilities.workspace.application.archive import ArchiveWorkspace
from untaped.capabilities.workspace.application.provision import ProvisionRepos
from untaped.capabilities.workspace.application.status import WorkspaceStatus
from untaped.capabilities.workspace.domain import (
    RepoArg,
    ResolvedRepo,
    WorkspaceRecord,
    repo_identity,
)
from untaped.capabilities.workspace.infrastructure import LocalGitWorktrees, StateWorkspaceStore
from workspace.conftest import commit_in, git

pytestmark = pytest.mark.integration
T0 = datetime(2026, 10, 1, tzinfo=UTC)


class UrlCatalog:
    def resolve(self, ident: str) -> ResolvedRepo:
        owner, name = repo_identity(ident)
        return ResolvedRepo(url=ident, name=f"{owner}/{name}")


@dataclass(frozen=True)
class Env:
    record: WorkspaceRecord
    status: WorkspaceStatus
    archive: ArchiveWorkspace
    repo: Path


@pytest.fixture
def env(tmp_path: Path, make_upstream: Callable[..., Path]) -> Env:
    git_ = LocalGitWorktrees(tmp_path / "cache")
    store = StateWorkspaceStore()
    workspaces = tmp_path / "ws"
    ProvisionRepos(
        store,
        git_,
        UrlCatalog(),
        workspaces_dir=workspaces,
        branch_template="{name}",
        parallel=2,
        now=lambda: T0,
    ).create("J-1", [RepoArg(ident=str(make_upstream("api")))])
    record = store.get("J-1")
    assert record is not None
    return Env(
        record,
        WorkspaceStatus(git_, workspaces_dir=workspaces),
        ArchiveWorkspace(store, git_, workspaces_dir=workspaces, now=lambda: T0),
        workspaces / "J-1" / "api",
    )


def test_clean_new_workspace(env: Env) -> None:
    [row] = env.status(env.record)
    assert (row.state, row.blockers, row.branch) == ("ok", (), "J-1")
    assert row.target_path == env.repo


def test_dirty_and_unpushed_block(env: Env) -> None:
    commit_in(env.repo)
    (env.repo / "scratch.txt").write_text("x")
    [row] = env.status(env.record)
    assert row.blockers == ("uncommitted changes", "1 commit not pushed")


def test_a_plain_push_clears_the_blocker(env: Env) -> None:
    commit_in(env.repo)
    git(env.repo, "push", "-q")
    [row] = env.status(env.record)
    assert row.blockers == ()


def test_fetch_still_reports_ok(env: Env) -> None:
    [row] = env.status(env.record, fetch=True)
    assert row.state == "ok"


def test_hand_deleted_repo_is_missing(env: Env) -> None:
    shutil.rmtree(env.repo)
    [row] = env.status(env.record)
    assert row.state == "missing"


def test_deleted_cache_is_cache_missing(env: Env, tmp_path: Path) -> None:
    shutil.rmtree(tmp_path / "cache")
    [row] = env.status(env.record)
    assert row.state == "cache_missing"
    assert row.blockers == ("repo cache missing; local work cannot be checked",)


def test_archive_removes_and_records(env: Env) -> None:
    rows = env.archive(env.record, force=False)
    assert [r.action for r in rows if r.repo] == ["removed"]
    assert not env.repo.parent.exists()
    assert StateWorkspaceStore().get("J-1") is None
    assert [a.name for a in StateWorkspaceStore().archived()] == ["J-1"]


def test_archive_after_the_dir_was_deleted(env: Env) -> None:
    shutil.rmtree(env.repo.parent)
    assert all(r.action == "removed" for r in env.archive(env.record, force=False))


def test_archive_leaves_other_files(env: Env) -> None:
    (env.repo.parent / "notes.md").write_text("keep")
    rows = env.archive(env.record, force=False)
    [summary] = [r for r in rows if not r.repo]
    assert (summary.action, summary.target_path) == ("removed", env.repo.parent)
    assert "left other files" in summary.detail
    assert (env.repo.parent / "notes.md").exists()
    assert not env.repo.exists()


def test_archive_failure_keeps_the_record(env: Env) -> None:
    (env.repo / "scratch.txt").write_text("x")
    rows = env.archive(env.record, force=False)
    assert [r.action for r in rows if r.repo] == ["failed"]
    assert rows[0].error is not None
    assert StateWorkspaceStore().get("J-1") is not None
