"""Status and archive use cases with real git."""

from __future__ import annotations

import contextvars
import shutil
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from untaped_git.api import RepoFinder
from untaped_git.testing import GitRemote
from untaped_workspace.application.archive import ArchiveWorkspace
from untaped_workspace.application.provision import ProvisionRepos
from untaped_workspace.application.status import WorkspaceStatus
from untaped_workspace.domain.models import RepoArg, WorkspaceRecord
from untaped_workspace.infrastructure import LocalGitWorktrees, ProjectWorkspaceStore
from workspace_test_support import archived_records, commit_in, git, stored

T0 = datetime(2026, 10, 1, tzinfo=UTC)


@dataclass(frozen=True)
class Env:
    record: WorkspaceRecord
    status: WorkspaceStatus
    archive: ArchiveWorkspace
    repo: Path
    provision: ProvisionRepos


@pytest.fixture
def env(tmp_path: Path, store_root: Path, make_upstream: Callable[..., GitRemote]) -> Env:
    git_ = LocalGitWorktrees(plugin="workspace")
    workspaces = tmp_path / "ws"
    store = ProjectWorkspaceStore()
    provision = ProvisionRepos(
        store,
        git_,
        RepoFinder(),
        workspaces_dir=workspaces,
        branch_template="{name}",
        parallel=2,
        now=lambda: T0,
    )
    provision.create("J-1", [RepoArg(ident=make_upstream("api").url)])
    record = stored("J-1")
    assert record is not None
    return Env(
        record,
        WorkspaceStatus(git_, parallel=2),
        ArchiveWorkspace(store, git_, now=lambda: T0),
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


class _Recorder:
    """A progress handle that keeps each update's phase and counts."""

    def __init__(self) -> None:
        self.updates: list[tuple[str | None, int | None, int | None]] = []

    def update(
        self,
        message: str | None = None,
        *,
        phase: str | None = None,
        done: int | None = None,
        total: int | None = None,
    ) -> None:
        self.updates.append((phase, done, total))


def test_fetching_one_repo_passes_the_handle_to_the_store(env: Env) -> None:
    progress = _Recorder()
    env.status(env.record, fetch=True, progress=progress)
    assert {phase for phase, _, _ in progress.updates} > {"J-1"}
    assert progress.updates[-1] == ("J-1", 1, 1)


def test_fetching_two_repos_reports_only_the_count_of_repos(
    env: Env, make_upstream: Callable[..., GitRemote]
) -> None:
    env.provision.add(env.record, [RepoArg(ident=make_upstream("web").url)])
    record = stored("J-1")
    assert record is not None
    progress = _Recorder()
    env.status(record, fetch=True, progress=progress)
    assert progress.updates == [("J-1", 1, 2), ("J-1", 2, 2)]


def test_hand_deleted_repo_is_missing(env: Env) -> None:
    shutil.rmtree(env.repo)
    [row] = env.status(env.record)
    assert row.state == "missing"


def test_read_only_commit_blocks(
    tmp_path: Path, store_root: Path, make_upstream: Callable[..., GitRemote]
) -> None:
    git_ = LocalGitWorktrees(plugin="workspace")
    store = ProjectWorkspaceStore()
    ProvisionRepos(
        store,
        git_,
        RepoFinder(),
        workspaces_dir=tmp_path / "ws",
        branch_template="{name}",
        parallel=1,
        now=lambda: T0,
    ).create("J-2", [RepoArg(ident=make_upstream("web").url, read_only=True)])
    commit_in(tmp_path / "ws" / "J-2" / "web")
    record = stored("J-2")
    assert record is not None
    [row] = WorkspaceStatus(git_, parallel=1)(record)
    assert row.blockers == ("1 commit not pushed",)


def test_a_repo_gone_from_the_store_is_cache_missing(env: Env, store_root: Path) -> None:
    shutil.rmtree(store_root)
    [row] = env.status(env.record)
    assert row.state == "cache_missing"
    assert row.blockers == ("repo missing from the repo store; local work cannot be checked",)


def test_archive_removes_the_worktrees_and_keeps_the_project(env: Env) -> None:
    rows = env.archive(env.record, force=False)
    assert [r.action for r in rows if r.repo] == ["removed"]
    assert not env.repo.exists()
    assert (env.repo.parent / ".untaped" / "project.json").is_file()
    assert stored("J-1") is None
    [archived] = archived_records()
    assert (archived.name, archived.meta and archived.meta.archived_at) == ("J-1", T0)
    assert [spec.dir for spec in archived.repos] == ["api"]  # the rows stay, to resume


def test_archive_after_the_worktree_was_deleted(env: Env) -> None:
    shutil.rmtree(env.repo)
    assert [r.action for r in env.archive(env.record, force=False) if r.repo] == ["removed"]


def test_archive_leaves_other_files(env: Env) -> None:
    (env.repo.parent / "notes.md").write_text("keep")
    rows = env.archive(env.record, force=False)
    [summary] = [r for r in rows if not r.repo]
    assert (summary.action, summary.target_path) == ("archived", env.repo.parent)
    assert "workspace create J-1" in summary.detail
    assert (env.repo.parent / "notes.md").exists()
    assert not env.repo.exists()


def test_archive_failure_keeps_the_record(env: Env) -> None:
    (env.repo / "scratch.txt").write_text("x")
    rows = env.archive(env.record, force=False)
    assert [r.action for r in rows if r.repo] == ["failed"]
    assert rows[0].error is not None
    assert stored("J-1") is not None


def test_archive_removes_repos_added_after_its_record_was_read(
    env: Env, make_upstream: Callable[..., GitRemote]
) -> None:
    env.provision.add(env.record, [RepoArg(ident=make_upstream("web").url)])
    with env.archive.hold(env.record) as record:  # env.record predates the add
        rows = env.archive(record, force=False)
    assert [(r.repo, r.action) for r in rows if r.repo] == [
        ("acme/api", "removed"),
        ("acme/web", "removed"),
    ]
    assert not env.repo.exists()
    [archived] = archived_records()
    assert [spec.dir for spec in archived.repos] == ["api", "web"]


class _GatedGit:
    """Real git whose ``remove`` signals ``removing``, then waits for ``release``."""

    def __init__(self, inner: LocalGitWorktrees) -> None:
        self.inner = inner
        self.removing = threading.Event()
        self.release = threading.Event()

    def __getattr__(self, name: str) -> object:
        return getattr(self.inner, name)

    def remove(self, url: str, dest: Path, *, base: str, force: bool) -> None:
        self.removing.set()
        assert self.release.wait(10)
        self.inner.remove(url, dest, base=base, force=force)


def test_add_during_archive_waits_then_resumes_the_workspace(
    tmp_path: Path, store_root: Path, make_upstream: Callable[..., GitRemote]
) -> None:
    workspaces = tmp_path / "ws"
    real = LocalGitWorktrees(plugin="workspace")
    store = ProjectWorkspaceStore()
    provision = ProvisionRepos(
        store,
        real,
        RepoFinder(),
        workspaces_dir=workspaces,
        branch_template="{name}",
        parallel=1,
        now=lambda: T0,
    )
    provision.create("J-1", [RepoArg(ident=make_upstream("api").url)])
    web = RepoArg(ident=make_upstream("web").url)
    record = stored("J-1")
    assert record is not None
    gated = _GatedGit(real)
    archive = ArchiveWorkspace(store, gated, now=lambda: T0)  # type: ignore[arg-type]

    def archive_held() -> None:
        with archive.hold(record) as held:
            archive(held, force=False)

    archiver = threading.Thread(target=contextvars.copy_context().run, args=(archive_held,))
    archiver.start()
    assert gated.removing.wait(10)
    errors: list[BaseException] = []

    def add() -> None:
        try:
            provision.add(record, [web])
        except BaseException as exc:
            errors.append(exc)

    adder = threading.Thread(target=contextvars.copy_context().run, args=(add,))
    adder.start()
    adder.join(0.5)  # time enough for an unserialised add to check out
    gated.release.set()
    archiver.join()
    adder.join()
    assert errors == []
    assert (workspaces / "J-1" / "api" / "README.md").exists()  # checked out again
    assert (workspaces / "J-1" / "web" / "README.md").exists()
    resumed = stored("J-1")
    assert resumed is not None and [spec.dir for spec in resumed.repos] == ["api", "web"]
