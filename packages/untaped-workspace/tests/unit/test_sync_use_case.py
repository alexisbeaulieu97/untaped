"""Unit tests for SyncWorkspace, using a stub GitRunner."""

from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest
from untaped_workspace.application import SyncWorkspace
from untaped_workspace.domain import (
    ManifestDefaults,
    Repo,
    RepoStatus,
    Workspace,
    WorkspaceManifest,
)
from untaped_workspace.errors import GitError, WorkspaceError
from untaped_workspace.infrastructure import LocalFilesystem, ManifestRepository

_FS = LocalFilesystem()


class _StubGit:
    def __init__(
        self,
        *,
        on_disk: Iterable[str] = (),
        statuses: dict[str, RepoStatus] | None = None,
        clone_fail: set[str] = frozenset(),
        fetch_fail: bool = False,
        local_fetch_fail: set[str] = frozenset(),
    ) -> None:
        self.events: list[tuple[str, Any]] = []
        self._on_disk = set(on_disk)
        self._statuses = statuses or {}
        self._clone_fail = clone_fail
        self._fetch_fail = fetch_fail
        self._local_fetch_fail = local_fetch_fail

    def ensure_bare(self, url: str, *, cache_dir: Path | None = None) -> Path:
        self.events.append(("ensure_bare", url))
        return Path(f"/tmp/cache/{url.split('/')[-1]}")

    def bare_fetch(self, bare_path: Path) -> None:
        self.events.append(("bare_fetch", bare_path))
        if self._fetch_fail:
            raise GitError("network down")

    def clone_with_reference(
        self, *, url: str, dest: Path, bare: Path, branch: str | None = None
    ) -> None:
        self.events.append(("clone", str(dest), branch))
        if dest.name in self._clone_fail:
            raise GitError("clone failed")
        self._on_disk.add(dest.name)
        dest.mkdir(parents=True, exist_ok=True)

    def fetch(self, repo_path: Path) -> None:
        self.events.append(("fetch", repo_path.name))
        if repo_path.name in self._local_fetch_fail:
            raise GitError("network down")

    def status(self, repo_path: Path) -> RepoStatus:
        self.events.append(("status", repo_path.name))
        return self._statuses.get(repo_path.name, RepoStatus(branch="main"))

    def ff_only_pull(self, repo_path: Path, *, branch: str) -> None:
        self.events.append(("pull", repo_path.name, branch))


def _seed_workspace(tmp_path: Path, manifest: WorkspaceManifest) -> Workspace:
    ws_path = tmp_path / "prod"
    ws_path.mkdir()
    ManifestRepository().write(ws_path, manifest)
    return Workspace(name="prod", path=ws_path)


def test_clones_missing_repo(tmp_path: Path) -> None:
    workspace = _seed_workspace(
        tmp_path,
        WorkspaceManifest(repos=[Repo(url="https://x/svc-a.git")]),
    )
    git = _StubGit()
    outcomes = SyncWorkspace(ManifestRepository(), git, fs=_FS)(workspace)
    assert outcomes[0].action == "clone"
    assert any(e[0] == "clone" for e in git.events)


def test_uses_target_branch_on_clone(tmp_path: Path) -> None:
    workspace = _seed_workspace(
        tmp_path,
        WorkspaceManifest(
            defaults=ManifestDefaults(branch="develop"),
            repos=[Repo(url="https://x/svc-a.git")],
        ),
    )
    git = _StubGit()
    SyncWorkspace(ManifestRepository(), git, fs=_FS)(workspace)
    clone_event = next(e for e in git.events if e[0] == "clone")
    assert clone_event[2] == "develop"


def test_per_repo_branch_overrides_default(tmp_path: Path) -> None:
    workspace = _seed_workspace(
        tmp_path,
        WorkspaceManifest(
            defaults=ManifestDefaults(branch="main"),
            repos=[Repo(url="https://x/svc-a.git", branch="feature/x")],
        ),
    )
    git = _StubGit()
    SyncWorkspace(ManifestRepository(), git, fs=_FS)(workspace)
    clone_event = next(e for e in git.events if e[0] == "clone")
    assert clone_event[2] == "feature/x"


def test_skips_dirty_existing_repo(tmp_path: Path) -> None:
    workspace = _seed_workspace(
        tmp_path,
        WorkspaceManifest(repos=[Repo(url="https://x/svc-a.git")]),
    )
    (workspace.path / "svc-a").mkdir()
    git = _StubGit(
        on_disk=["svc-a"],
        statuses={"svc-a": RepoStatus(branch="main", modified=2)},
    )
    outcomes = SyncWorkspace(ManifestRepository(), git, fs=_FS)(workspace)
    assert outcomes[0].action == "skip"
    assert "dirty" in outcomes[0].detail


def test_skips_diverged_repo(tmp_path: Path) -> None:
    workspace = _seed_workspace(
        tmp_path,
        WorkspaceManifest(repos=[Repo(url="https://x/svc-a.git")]),
    )
    (workspace.path / "svc-a").mkdir()
    git = _StubGit(
        on_disk=["svc-a"],
        statuses={"svc-a": RepoStatus(branch="main", ahead=2, behind=3)},
    )
    outcomes = SyncWorkspace(ManifestRepository(), git, fs=_FS)(workspace)
    assert outcomes[0].action == "skip"
    assert "diverged" in outcomes[0].detail


def test_skips_wrong_branch_when_target_set(tmp_path: Path) -> None:
    workspace = _seed_workspace(
        tmp_path,
        WorkspaceManifest(
            defaults=ManifestDefaults(branch="main"),
            repos=[Repo(url="https://x/svc-a.git")],
        ),
    )
    (workspace.path / "svc-a").mkdir()
    git = _StubGit(
        on_disk=["svc-a"],
        statuses={"svc-a": RepoStatus(branch="feature/x")},
    )
    outcomes = SyncWorkspace(ManifestRepository(), git, fs=_FS)(workspace)
    assert outcomes[0].action == "skip"
    assert "expected main" in outcomes[0].detail


def test_pulls_when_behind_clean(tmp_path: Path) -> None:
    workspace = _seed_workspace(
        tmp_path,
        WorkspaceManifest(repos=[Repo(url="https://x/svc-a.git")]),
    )
    (workspace.path / "svc-a").mkdir()
    git = _StubGit(
        on_disk=["svc-a"],
        statuses={"svc-a": RepoStatus(branch="main", behind=3)},
    )
    outcomes = SyncWorkspace(ManifestRepository(), git, fs=_FS)(workspace)
    assert outcomes[0].action == "pull"
    assert "3 commits" in outcomes[0].detail
    assert ("pull", "svc-a", "main") in git.events


def test_up_to_date(tmp_path: Path) -> None:
    workspace = _seed_workspace(
        tmp_path,
        WorkspaceManifest(repos=[Repo(url="https://x/svc-a.git")]),
    )
    (workspace.path / "svc-a").mkdir()
    git = _StubGit(
        on_disk=["svc-a"],
        statuses={"svc-a": RepoStatus(branch="main")},
    )
    outcomes = SyncWorkspace(ManifestRepository(), git, fs=_FS)(workspace)
    assert outcomes[0].action == "up-to-date"


def test_only_filters_repos(tmp_path: Path) -> None:
    workspace = _seed_workspace(
        tmp_path,
        WorkspaceManifest(
            repos=[
                Repo(url="https://x/svc-a.git"),
                Repo(url="https://x/svc-b.git"),
                Repo(url="https://x/svc-c.git"),
            ],
        ),
    )
    git = _StubGit()
    outcomes = SyncWorkspace(ManifestRepository(), git, fs=_FS)(workspace, only=["svc-b"])
    assert [o.repo for o in outcomes] == ["svc-b"]


def test_only_rejects_unknown_identifier(tmp_path: Path) -> None:
    workspace = _seed_workspace(
        tmp_path,
        WorkspaceManifest(
            repos=[
                Repo(url="https://x/svc-a.git"),
                Repo(url="https://x/svc-b.git"),
            ],
        ),
    )
    git = _StubGit()
    with pytest.raises(WorkspaceError) as excinfo:
        SyncWorkspace(ManifestRepository(), git, fs=_FS)(
            workspace, only=["svc-b", "typo", "also-typo"]
        )
    msg = str(excinfo.value)
    assert "typo" in msg
    assert "also-typo" in msg
    # And no git work should have happened.
    assert git.events == []


def test_prune_removes_orphaned_clones(tmp_path: Path) -> None:
    workspace = _seed_workspace(
        tmp_path,
        WorkspaceManifest(repos=[Repo(url="https://x/svc-a.git")]),
    )
    # Pre-populate svc-a (declared) and svc-old (orphan)
    (workspace.path / "svc-a").mkdir()
    orphan = workspace.path / "svc-old"
    orphan.mkdir()
    (orphan / ".git").mkdir()

    git = _StubGit(
        on_disk=["svc-a", "svc-old"],
        statuses={
            "svc-a": RepoStatus(branch="main"),
            "svc-old": RepoStatus(branch="main"),
        },
    )
    outcomes = SyncWorkspace(ManifestRepository(), git, fs=_FS)(workspace, prune=True)
    actions = {o.repo: o.action for o in outcomes}
    assert actions["svc-old"] == "remove"
    assert not orphan.exists()


def test_prune_skips_dirty_orphan(tmp_path: Path) -> None:
    workspace = _seed_workspace(
        tmp_path,
        WorkspaceManifest(repos=[]),
    )
    orphan = workspace.path / "svc-old"
    orphan.mkdir()
    (orphan / ".git").mkdir()

    git = _StubGit(
        on_disk=["svc-old"],
        statuses={"svc-old": RepoStatus(branch="main", modified=1)},
    )
    outcomes = SyncWorkspace(ManifestRepository(), git, fs=_FS)(workspace, prune=True)
    assert outcomes[0].action == "skip"
    assert orphan.exists()


def test_clone_failure_yields_skip(tmp_path: Path) -> None:
    workspace = _seed_workspace(
        tmp_path,
        WorkspaceManifest(repos=[Repo(url="https://x/svc-a.git")]),
    )
    git = _StubGit(clone_fail={"svc-a"})
    outcomes = SyncWorkspace(ManifestRepository(), git, fs=_FS)(workspace)
    assert outcomes[0].action == "skip"


def test_fetch_failure_yields_skip(tmp_path: Path) -> None:
    workspace = _seed_workspace(
        tmp_path,
        WorkspaceManifest(repos=[Repo(url="https://x/svc-a.git")]),
    )
    git = _StubGit(fetch_fail=True)
    outcomes = SyncWorkspace(ManifestRepository(), git, fs=_FS)(workspace)
    assert outcomes[0].action == "skip"
    assert "cache fetch failed" in outcomes[0].detail


def test_existing_clone_is_fetched_before_status(tmp_path: Path) -> None:
    """`status.behind` reads `origin/<branch>` from the working clone — that
    ref is stale unless we fetch the clone first."""
    workspace = _seed_workspace(
        tmp_path,
        WorkspaceManifest(repos=[Repo(url="https://x/svc-a.git")]),
    )
    (workspace.path / "svc-a").mkdir()  # existing clone
    git = _StubGit(on_disk=["svc-a"])
    SyncWorkspace(ManifestRepository(), git, fs=_FS)(workspace)

    op_names = [event[0] for event in git.events]
    fetch_idx = op_names.index("fetch")
    status_idx = op_names.index("status")
    assert fetch_idx < status_idx


def test_fresh_clone_does_not_call_local_fetch(tmp_path: Path) -> None:
    """A brand-new clone shouldn't be redundantly fetched after clone."""
    workspace = _seed_workspace(
        tmp_path,
        WorkspaceManifest(repos=[Repo(url="https://x/svc-a.git")]),
    )
    git = _StubGit()
    SyncWorkspace(ManifestRepository(), git, fs=_FS)(workspace)
    op_names = [event[0] for event in git.events]
    assert "clone" in op_names
    assert "fetch" not in op_names


def test_local_fetch_failure_yields_skip(tmp_path: Path) -> None:
    """A network-flaky `git fetch` on an existing clone is a skip, not abort."""
    workspace = _seed_workspace(
        tmp_path,
        WorkspaceManifest(repos=[Repo(url="https://x/svc-a.git")]),
    )
    (workspace.path / "svc-a").mkdir()
    git = _StubGit(on_disk=["svc-a"], local_fetch_fail={"svc-a"})
    outcomes = SyncWorkspace(ManifestRepository(), git, fs=_FS)(workspace)
    assert outcomes[0].action == "skip"
    assert "fetch failed" in (outcomes[0].detail or "")


@pytest.fixture
def _ensure_iterable() -> None:  # placeholder to keep import order tidy
    pass
