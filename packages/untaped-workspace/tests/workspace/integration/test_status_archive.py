"""Status and archive use cases with real git."""

from __future__ import annotations

import shutil
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from untaped_workspace.application.archive import ArchiveWorkspace
from untaped_workspace.application.provision import ProvisionRepos
from untaped_workspace.application.status import WorkspaceStatus
from untaped_workspace.domain import (
    RepoArg,
    ResolvedRepo,
    WorkspaceRecord,
    repo_identity,
)
from untaped_workspace.errors import WorkspaceNotFoundError
from untaped_workspace.infrastructure import LocalGitWorktrees, StateWorkspaceStore
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
    provision: ProvisionRepos


@pytest.fixture
def env(tmp_path: Path, make_upstream: Callable[..., Path]) -> Env:
    git_ = LocalGitWorktrees(tmp_path / "cache")
    workspaces = tmp_path / "ws"
    store = StateWorkspaceStore(workspaces_dir=workspaces)
    provision = ProvisionRepos(
        store,
        git_,
        UrlCatalog(),
        workspaces_dir=workspaces,
        branch_template="{name}",
        parallel=2,
        now=lambda: T0,
    )
    provision.create("J-1", [RepoArg(ident=str(make_upstream("api")))])
    record = store.get("J-1")
    assert record is not None
    return Env(
        record,
        WorkspaceStatus(git_, workspaces_dir=workspaces, parallel=2),
        ArchiveWorkspace(store, git_, workspaces_dir=workspaces, now=lambda: T0),
        workspaces / "J-1" / "api",
        provision,
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


def test_read_only_commit_blocks(tmp_path: Path, make_upstream: Callable[..., Path]) -> None:
    git_ = LocalGitWorktrees(tmp_path / "cache")
    store = StateWorkspaceStore(workspaces_dir=tmp_path / "ws")
    ProvisionRepos(
        store,
        git_,
        UrlCatalog(),
        workspaces_dir=tmp_path / "ws",
        branch_template="{name}",
        parallel=1,
        now=lambda: T0,
    ).create("J-2", [RepoArg(ident=str(make_upstream("web")), read_only=True)])
    commit_in(tmp_path / "ws" / "J-2" / "web")
    record = store.get("J-2")
    assert record is not None
    [row] = WorkspaceStatus(git_, workspaces_dir=tmp_path / "ws", parallel=1)(record)
    assert row.blockers == ("1 commit not pushed",)


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
    assert (summary.action, summary.target_path) == ("skipped", env.repo.parent)
    assert "left other files" in summary.detail
    assert (env.repo.parent / "notes.md").exists()
    assert not env.repo.exists()


def test_archive_failure_keeps_the_record(env: Env) -> None:
    (env.repo / "scratch.txt").write_text("x")
    rows = env.archive(env.record, force=False)
    assert [r.action for r in rows if r.repo] == ["failed"]
    assert rows[0].error is not None
    assert StateWorkspaceStore().get("J-1") is not None


def test_archive_removes_repos_added_after_its_record_was_read(
    env: Env, make_upstream: Callable[..., Path]
) -> None:
    env.provision.add(env.record, [RepoArg(ident=str(make_upstream("web")))])
    with env.archive.hold(env.record.name) as record:  # env.record predates the add
        rows = env.archive(record, force=False)
    assert [(r.repo, r.action) for r in rows if r.repo] == [
        ("acme/api", "removed"),
        ("acme/web", "removed"),
    ]
    assert not env.repo.parent.exists()
    [archived] = StateWorkspaceStore().archived()
    assert [spec.dir for spec in archived.repos] == ["api", "web"]


class _GatedGit:
    """Real git whose ``remove`` signals ``removing``, then waits for ``release``."""

    def __init__(self, inner: LocalGitWorktrees) -> None:
        self.inner = inner
        self.removing = threading.Event()
        self.release = threading.Event()

    def __getattr__(self, name: str) -> object:
        return getattr(self.inner, name)

    def remove(self, url: str, dest: Path, *, force: bool) -> None:
        self.removing.set()
        assert self.release.wait(10)
        self.inner.remove(url, dest, force=force)


def test_add_during_archive_waits_then_fails_without_a_worktree(
    tmp_path: Path, make_upstream: Callable[..., Path]
) -> None:
    workspaces = tmp_path / "ws"
    real = LocalGitWorktrees(tmp_path / "cache")
    store = StateWorkspaceStore(workspaces_dir=workspaces)
    provision = ProvisionRepos(
        store,
        real,
        UrlCatalog(),
        workspaces_dir=workspaces,
        branch_template="{name}",
        parallel=1,
        now=lambda: T0,
    )
    provision.create("J-1", [RepoArg(ident=str(make_upstream("api")))])
    web = RepoArg(ident=str(make_upstream("web")))
    record = store.get("J-1")
    assert record is not None
    gated = _GatedGit(real)
    archive = ArchiveWorkspace(store, gated, workspaces_dir=workspaces, now=lambda: T0)  # type: ignore[arg-type]

    def archive_held() -> None:
        with archive.hold("J-1") as held:
            archive(held, force=False)

    archiver = threading.Thread(target=archive_held)
    archiver.start()
    assert gated.removing.wait(10)
    errors: list[BaseException] = []

    def add() -> None:
        try:
            provision.add(record, [web])
        except BaseException as exc:
            errors.append(exc)

    adder = threading.Thread(target=add)
    adder.start()
    adder.join(0.5)  # time enough for an unserialised add to check out
    gated.release.set()
    archiver.join()
    adder.join()
    assert [type(exc) for exc in errors] == [WorkspaceNotFoundError]
    assert not (workspaces / "J-1" / "web").exists()
    assert store.get("J-1") is None
    [archived] = store.archived()
    assert [spec.dir for spec in archived.repos] == ["api"]
