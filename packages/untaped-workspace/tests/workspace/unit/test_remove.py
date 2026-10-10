"""RemoveWorkspace: the plan, the refusals and the release, with a fake repo store."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from untaped_workspace.application.remove import RemoveWorkspace, refusal_hint
from untaped_workspace.domain import (
    LocalBranch,
    RepoRelease,
    RepoSpec,
    StoreUse,
    WorkspaceRecord,
    releasable_branches,
    unpushed_branch_blocker,
)
from untaped_workspace.domain.records import RemoveOutcome, StatusRow
from untaped_workspace.errors import GitError, WorkspaceNotFoundError
from untaped_workspace.infrastructure import StateWorkspaceStore

T0 = datetime(2026, 10, 1, tzinfo=UTC)
API = "https://git.example/acme/api.git"
WEB = "https://git.example/acme/web.git"


def _spec(url: str, branch: str | None = "J-1") -> RepoSpec:
    name = url.rsplit("/", 1)[1].removesuffix(".git")
    return RepoSpec(url=url, name=f"acme/{name}", dir=name, branch=branch, base="main")


def _branch(
    name: str, *, checked_out: bool = False, unpushed: int = 0, stashed: bool = False
) -> LocalBranch:
    return LocalBranch(name=name, checked_out=checked_out, unpushed=unpushed, stashed=stashed)


class FakeGit:
    """The parts of ``GitWorktrees`` that ``remove`` uses."""

    def __init__(self, uses: Mapping[str, StoreUse | None] | None = None) -> None:
        self.uses = dict(uses or {})
        self.removed: list[tuple[str, bool]] = []
        self.released: list[tuple[str, tuple[str, ...]]] = []
        self.asked: list[str] = []
        self.remove_error: Exception | None = None
        self.release_error: Exception | None = None

    def remove(self, url: str, dest: Path, *, force: bool) -> None:
        if self.remove_error is not None:
            raise self.remove_error
        self.removed.append((url, force))

    def store_use(self, url: str) -> StoreUse | None:
        self.asked.append(url)
        return self.uses.get(url, StoreUse(branches=(), worktrees=0))

    def release(self, url: str, *, branches: Sequence[str]) -> RepoRelease:
        if self.release_error is not None:
            raise self.release_error
        self.released.append((url, tuple(branches)))
        return RepoRelease(action="removed", detail="1.0 KiB freed", freed_bytes=1024)


class FakeStatus:
    """``WorkspaceStatus`` answering each repo's blockers from ``blockers``."""

    def __init__(self, blockers: Mapping[str, tuple[str, ...]] | None = None) -> None:
        self.blockers = dict(blockers or {})

    def __call__(self, record: WorkspaceRecord, *, fetch: bool = False) -> list[StatusRow]:
        return [
            StatusRow(
                workspace=record.name,
                repo=spec.name,
                dir=spec.dir,
                branch=spec.branch,
                base=spec.base,
                read_only=spec.read_only,
                state="ok",
                blockers=self.blockers.get(spec.url, ()),
                target_path=Path("/ws") / record.name / spec.dir,
            )
            for spec in record.repos
        ]


@pytest.fixture
def store(tmp_path: Path) -> StateWorkspaceStore:
    return StateWorkspaceStore(workspaces_dir=tmp_path / "ws")


def _remover(
    store: StateWorkspaceStore,
    git: FakeGit,
    tmp_path: Path,
    status: FakeStatus | None = None,
) -> RemoveWorkspace:
    fake: Any = git
    return RemoveWorkspace(
        store,
        fake,
        status=status or FakeStatus(),  # type: ignore[arg-type]
        workspaces_dir=tmp_path / "ws",
        now=lambda: T0,
    )


def _add(store: StateWorkspaceStore, name: str, *specs: RepoSpec, archive: bool = False) -> None:
    store.create(WorkspaceRecord(name=name, created_at=T0))
    store.add_repos(name, specs)
    if archive:
        store.archive(name, at=T0)


def _remove(
    remover: RemoveWorkspace, name: str, *, force: bool = False
) -> list[tuple[str, str, str]]:
    with remover.hold(name) as plan:
        rows = remover(plan, force=force)
    return [(row.repo, row.action, row.detail) for row in rows]


def test_an_unknown_name_is_not_found_and_names_the_known_ones(
    store: StateWorkspaceStore, tmp_path: Path
) -> None:
    _add(store, "J-1", _spec(API))
    _add(store, "J-0", _spec(WEB), archive=True)
    with (
        pytest.raises(WorkspaceNotFoundError) as caught,
        _remover(store, FakeGit(), tmp_path).hold("nope"),
    ):
        pass
    assert str(caught.value) == "workspace not found: 'nope'; known: J-0, J-1"


def test_an_active_workspace_is_archived_then_every_record_released(
    store: StateWorkspaceStore, tmp_path: Path
) -> None:
    _add(store, "J-1", _spec(API), archive=True)
    _add(store, "J-1", _spec(API), _spec(WEB))
    use = StoreUse(branches=(_branch("J-1"), _branch("old", unpushed=0)), worktrees=0)
    git = FakeGit({API: use, WEB: StoreUse(branches=(), worktrees=0)})

    rows = _remove(_remover(store, git, tmp_path), "J-1")

    assert rows == [
        ("acme/api", "removed", "1.0 KiB freed"),
        ("acme/web", "removed", "1.0 KiB freed"),
        ("", "removed", "workspace record and directory"),
    ]
    assert git.removed == [(API, False), (WEB, False)]
    assert git.released == [(API, ("J-1", "old")), (WEB, ())]
    assert store.active() == [] and store.archived() == []


def test_active_blockers_refuse_and_force_discards(
    store: StateWorkspaceStore, tmp_path: Path
) -> None:
    _add(store, "J-1", _spec(API))
    git = FakeGit()
    remover = _remover(store, git, tmp_path, FakeStatus({API: ("2 commits not pushed",)}))

    with remover.hold("J-1") as plan:
        assert [repo.blockers for repo in plan.blocked] == [("2 commits not pushed",)]
        preview = remover.preview(plan, force=False)
    assert [(row.repo, row.action) for row in preview] == [("acme/api", "skipped"), ("", "planned")]
    assert preview[0].detail == "2 commits not pushed; release from the repo store"
    assert "commit and push" in refusal_hint(plan)
    assert git.removed == [] and store.get("J-1") is not None

    _remove(remover, "J-1", force=True)
    assert git.removed == [(API, True)]
    assert store.get("J-1") is None


def test_an_archived_branch_with_unpushed_commits_blocks(
    store: StateWorkspaceStore, tmp_path: Path
) -> None:
    _add(store, "J-1", _spec(API), archive=True)
    use = StoreUse(branches=(_branch("J-1", unpushed=2), _branch("other", unpushed=5)), worktrees=0)
    git = FakeGit({API: use})
    remover = _remover(store, git, tmp_path)

    with remover.hold("J-1") as plan:
        # Only the workspace's own branch: another branch's commits are not its work.
        assert [repo.blockers for repo in plan.blocked] == [("branch J-1: 2 commits not pushed",)]
        hint = refusal_hint(plan)
    assert hint.startswith("push each unpushed branch")
    assert hint.endswith("or pass --force to delete it")

    assert _remove(remover, "J-1", force=True)[0] == ("acme/api", "removed", "1.0 KiB freed")
    assert git.released == [(API, ("J-1", "other"))]


def test_a_repo_another_workspace_names_is_kept_without_asking_the_store(
    store: StateWorkspaceStore, tmp_path: Path
) -> None:
    _add(store, "J-1", _spec(API), _spec(WEB), archive=True)
    _add(store, "J-2", _spec(API.replace("acme", "ACME")), archive=True)
    git = FakeGit()
    remover = _remover(store, git, tmp_path)

    with remover.hold("J-1") as plan:
        assert [repo.held_by for repo in plan.repos] == ["J-2", None]
        preview = remover.preview(plan, force=False)
        rows = remover(plan, force=False)

    assert preview[0].detail == "kept: used by workspace J-2"
    assert [(row.repo, row.action, row.detail) for row in rows] == [
        ("acme/api", "kept", "used by workspace J-2"),
        ("acme/web", "removed", "1.0 KiB freed"),
        ("", "removed", "workspace record"),
    ]
    assert git.asked == [WEB, WEB]  # the plan's look and the release's
    assert git.released == [(WEB, ())]
    assert [record.name for record in store.archived()] == ["J-2"]


def test_a_repo_missing_from_the_store_is_skipped(
    store: StateWorkspaceStore, tmp_path: Path
) -> None:
    _add(store, "J-1", _spec(API), archive=True)
    remover = _remover(store, FakeGit({API: None}), tmp_path)
    with remover.hold("J-1") as plan:
        assert remover.preview(plan, force=False)[0].detail == "not in the repo store"
        rows = remover(plan, force=False)
    assert (rows[0].action, rows[0].detail) == ("skipped", "not in the repo store")


def test_a_workspace_worktree_registered_meanwhile_keeps_the_repo(
    store: StateWorkspaceStore, tmp_path: Path
) -> None:
    _add(store, "J-1", _spec(API), archive=True)
    git = FakeGit({API: StoreUse(branches=(_branch("J-1"),), worktrees=1)})
    assert _remove(_remover(store, git, tmp_path), "J-1")[0] == (
        "acme/api",
        "kept",
        "used by 1 workspace worktree",
    )
    assert git.released == []


def test_a_failed_release_is_a_failed_row_and_the_records_still_go(
    store: StateWorkspaceStore, tmp_path: Path
) -> None:
    _add(store, "J-1", _spec(API), archive=True)
    git = FakeGit()
    git.release_error = GitError("lock held", category="conflict")
    remover = _remover(store, git, tmp_path)
    with remover.hold("J-1") as plan:
        rows = remover(plan, force=False)
    assert [(row.action, row.detail) for row in rows] == [
        ("failed", "lock held"),
        ("removed", "workspace record"),
    ]
    assert rows[0].error is not None
    assert store.archived() == []


def test_a_worktree_that_fails_to_go_keeps_every_record(
    store: StateWorkspaceStore, tmp_path: Path
) -> None:
    _add(store, "J-1", _spec(API), archive=True)
    _add(store, "J-1", _spec(API))
    git = FakeGit()
    git.remove_error = GitError("busy", category="failed")
    remover = _remover(store, git, tmp_path)
    with remover.hold("J-1") as plan:
        rows = remover(plan, force=False)
    assert [(row.repo, row.action, row.detail) for row in rows] == [("acme/api", "failed", "busy")]
    assert all(isinstance(row, RemoveOutcome) and row.error is not None for row in rows)
    assert store.get("J-1") is not None and len(store.archived()) == 1
    assert git.released == []


def test_a_directory_with_other_files_stays(store: StateWorkspaceStore, tmp_path: Path) -> None:
    _add(store, "J-1", _spec(API))
    (tmp_path / "ws" / "J-1").mkdir(parents=True)
    (tmp_path / "ws" / "J-1" / "notes.txt").write_text("mine")
    rows = _remove(_remover(store, FakeGit(), tmp_path), "J-1")
    assert rows[-1][2].startswith("workspace record; left other files in ")


def test_the_dry_run_preview_of_an_active_workspace(
    store: StateWorkspaceStore, tmp_path: Path
) -> None:
    _add(store, "J-1", _spec(API))
    remover = _remover(store, FakeGit(), tmp_path)
    with remover.hold("J-1") as plan:
        rows = remover.preview(plan, force=False)
    assert [(row.repo, row.action, row.detail) for row in rows] == [
        ("acme/api", "planned", "release from the repo store"),
        ("", "planned", "archive, then drop the workspace record"),
    ]


def test_releasable_branches_follow_the_rule() -> None:
    branches = (
        _branch("pushed"),
        _branch("ahead", unpushed=1),
        _branch("stashed", stashed=True),
        _branch("open", checked_out=True),
    )
    assert releasable_branches(branches, force=False) == ["pushed"]
    assert releasable_branches(branches, force=True) == ["pushed", "ahead", "stashed"]


def test_unpushed_branch_blocker() -> None:
    assert unpushed_branch_blocker(_branch("b")) is None
    assert unpushed_branch_blocker(_branch("b", unpushed=1)) == "branch b: 1 commit not pushed"
