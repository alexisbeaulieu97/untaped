"""Unit tests for SyncWorkspace, using a stub GitRunner."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from untaped.capabilities.workspace.application import (
    BareFetchTracker,
    RepoSyncEngine,
    SyncWorkspace,
    SyncWorkspaces,
)
from untaped.capabilities.workspace.application.ports import ManifestReader
from untaped.capabilities.workspace.domain import (
    BareCacheEntry,
    ManifestDefaults,
    Repo,
    RepoStatus,
    SyncOutcome,
    Workspace,
    WorkspaceManifest,
)
from untaped.capabilities.workspace.errors import GitError, UnmatchedRepoFilterError, WorkspaceError
from untaped.capabilities.workspace.infrastructure import LocalFilesystem, YamlManifestRepository
from workspace.conftest import StubGit

_FS = LocalFilesystem()
_SVC_A = WorkspaceManifest(repos=[Repo(url="https://x/svc-a.git")])


def _seed_workspace(tmp_path: Path, manifest: WorkspaceManifest, name: str = "prod") -> Workspace:
    ws_path = tmp_path / name
    ws_path.mkdir()
    YamlManifestRepository().write(ws_path, manifest)
    return Workspace(name=name, path=ws_path)


def _sync(git: StubGit, workspace: Workspace, cache_dir: Path, **kwargs: Any) -> list[SyncOutcome]:
    return SyncWorkspace(YamlManifestRepository(), git, fs=_FS, cache_dir=cache_dir)(
        workspace, **kwargs
    )


def _sync_and_prune(
    reader: ManifestReader, git: StubGit, workspace: Workspace, cache_dir: Path
) -> list[SyncOutcome]:
    """Sync, then prune orphans the way confirmed ``sync --prune`` does."""
    outcomes = SyncWorkspace(reader, git, fs=_FS, cache_dir=cache_dir)(workspace)
    engine = RepoSyncEngine(git, fs=_FS, cache_dir=cache_dir)
    skipped, candidates = SyncWorkspaces(reader, engine).plan_prune([workspace])
    return [*outcomes, *skipped, *(engine.prune_candidate(c) for c in candidates)]


def _ops(git: StubGit) -> list[str]:
    return [event[0] for event in git.events]


# ---- missing clone -----------------------------------------------------------


@pytest.mark.parametrize(
    ("defaults", "repo_branch", "expected"),
    [(None, None, None), ("develop", None, "develop"), ("main", "feature/x", "feature/x")],
)
def test_clones_missing_repo_on_target_branch(
    tmp_path: Path, defaults: str | None, repo_branch: str | None, expected: str | None
) -> None:
    manifest = WorkspaceManifest(
        defaults=ManifestDefaults(branch=defaults),
        repos=[Repo(url="https://x/svc-a.git", branch=repo_branch)],
    )
    git = StubGit()
    outcomes = _sync(git, _seed_workspace(tmp_path, manifest), tmp_path)
    assert outcomes[0].action == "cloned"
    assert next(e for e in git.events if e[0] == "clone")[2] == expected
    # A brand-new clone is not redundantly fetched after cloning.
    assert "fetch" not in _ops(git)


def test_skips_declared_dir_without_git_metadata(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path, _SVC_A)
    (workspace.path / "svc-a").mkdir()
    git = StubGit()
    outcomes = _sync(git, workspace, tmp_path)
    assert (outcomes[0].action, outcomes[0].detail) == ("skipped", "not a git repository")
    assert git.events == []


class _BareErrorStub(StubGit):
    def ensure_bare(self, url: str, *, cache_dir: Path) -> BareCacheEntry:
        self.events.append(("ensure_bare", url))
        raise GitError("permission denied")


@pytest.mark.parametrize(
    ("git", "detail"),
    [
        # Every step failure surfaces as ``<step> failed: <git error>``;
        # ``ensure_bare`` and ``bare_fetch`` share the bare-cache prefix.
        (StubGit(clone_fail={"svc-a"}), "clone failed: clone failed"),
        (StubGit(fetch_fail=True), "cache fetch failed: network down"),
        (_BareErrorStub(), "cache fetch failed: permission denied"),
    ],
)
def test_missing_clone_step_failure_yields_failed(
    tmp_path: Path, git: StubGit, detail: str
) -> None:
    outcomes = _sync(git, _seed_workspace(tmp_path, _SVC_A), tmp_path)
    assert (outcomes[0].action, outcomes[0].detail) == ("failed", detail)


# ---- existing clone ----------------------------------------------------------


@pytest.mark.parametrize(
    ("default_branch", "git", "action", "detail"),
    [
        (
            None,
            StubGit(statuses={"svc-a": RepoStatus(branch="x", upstream=None)}),
            "skipped",
            "no upstream",
        ),
        (
            None,
            StubGit(statuses={"svc-a": RepoStatus(branch="main", upstream="o/main", modified=2)}),
            "skipped",
            "dirty working tree",
        ),
        (
            None,
            StubGit(
                statuses={"svc-a": RepoStatus(branch="main", upstream="o/main", ahead=2, behind=3)}
            ),
            "skipped",
            "diverged",
        ),
        (
            "main",
            StubGit(statuses={"svc-a": RepoStatus(branch="feature/x")}),
            "skipped",
            "on feature/x, expected main",
        ),
        (
            "main",
            StubGit(statuses={"svc-a": RepoStatus(branch=None)}),
            "skipped",
            "on detached, expected main",
        ),
        (
            None,
            StubGit(statuses={"svc-a": RepoStatus(branch=None, behind=3)}),
            "skipped",
            "detached head",
        ),
        (
            None,
            StubGit(statuses={"svc-a": RepoStatus(branch="main", upstream="o/main", behind=3)}),
            "pulled",
            "3 commits",
        ),
        (None, StubGit(), "unchanged", ""),
        (None, StubGit(local_fetch_fail={"svc-a"}), "failed", "fetch failed: network down"),
        (None, StubGit(status_fail={"svc-a"}), "failed", "status failed: status failed"),
        (
            None,
            StubGit(
                statuses={"svc-a": RepoStatus(branch="main", upstream="o/main", behind=3)},
                pull_fail={"svc-a"},
            ),
            "failed",
            "ff-only pull failed: non-fast-forward pull",
        ),
    ],
)
def test_existing_clone_outcome(
    tmp_path: Path, default_branch: str | None, git: StubGit, action: str, detail: str
) -> None:
    manifest = WorkspaceManifest(
        defaults=ManifestDefaults(branch=default_branch), repos=[Repo(url="https://x/svc-a.git")]
    )
    workspace = _seed_workspace(tmp_path, manifest)
    (workspace.path / "svc-a" / ".git").mkdir(parents=True)
    outcomes = _sync(git, workspace, tmp_path)
    assert outcomes[0].action == action
    assert detail in outcomes[0].detail
    assert (("pull", "svc-a", "main") in git.events) == (
        action == "pulled" or "pull failed" in detail
    )


def test_existing_clone_fetches_own_remote_before_status(tmp_path: Path) -> None:
    """``status.behind`` reads ``origin/<branch>`` from the clone, which is stale
    unless fetched first; the bare cache only seeds missing clones."""
    workspace = _seed_workspace(tmp_path, _SVC_A)
    (workspace.path / "svc-a" / ".git").mkdir(parents=True)
    git = StubGit()
    _sync(git, workspace, tmp_path)
    ops = _ops(git)
    assert ops.index("fetch") < ops.index("status")
    assert "ensure_bare" not in ops
    assert "bare_fetch" not in ops


# ---- --repo filter -------------------------------------------------------------


def test_only_filters_repos(tmp_path: Path) -> None:
    manifest = WorkspaceManifest(
        repos=[Repo(url=f"https://x/svc-{n}.git") for n in "abc"],
    )
    outcomes = _sync(StubGit(), _seed_workspace(tmp_path, manifest), tmp_path, only=["svc-b"])
    assert [o.repo for o in outcomes] == ["svc-b"]


def test_only_rejects_unknown_identifier_before_git_work(tmp_path: Path) -> None:
    """Strict mode (default) raises the typed error carrying the unmatched
    identifiers; it still subclasses ``WorkspaceError`` for ``report_errors``."""
    manifest = WorkspaceManifest(
        repos=[Repo(url="https://x/svc-a.git"), Repo(url="https://x/svc-b.git")]
    )
    git = StubGit()
    with pytest.raises(UnmatchedRepoFilterError) as excinfo:
        _sync(
            git, _seed_workspace(tmp_path, manifest), tmp_path, only=["svc-b", "typo", "also-typo"]
        )
    assert excinfo.value.unmatched == ("also-typo", "typo")
    assert "also-typo" in str(excinfo.value)
    assert isinstance(excinfo.value, WorkspaceError)
    assert git.events == []


def test_only_unmatched_under_strict_false_yields_per_identifier_rows(tmp_path: Path) -> None:
    """Each unmatched identifier becomes its own ``unmatched`` row, even when a
    sibling identifier matched (typos must not be silent under ``--all``)."""
    manifest = WorkspaceManifest(
        repos=[Repo(url="https://x/svc-a.git"), Repo(url="https://x/svc-b.git")]
    )
    outcomes = _sync(
        StubGit(),
        _seed_workspace(tmp_path, manifest),
        tmp_path,
        only=["svc-a", "nonexistent", "also-typo"],
        strict_only=False,
    )
    assert sorted((o.repo, o.action) for o in outcomes) == [
        ("also-typo", "unmatched"),
        ("nonexistent", "unmatched"),
        ("svc-a", "cloned"),
    ]
    assert all(
        o.detail == "not in this workspace's manifest" for o in outcomes if o.action == "unmatched"
    )


# ---- prune -------------------------------------------------------------------


def test_prune_removes_orphaned_clones(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path, _SVC_A)
    (workspace.path / "svc-a" / ".git").mkdir(parents=True)
    orphan = workspace.path / "svc-old"
    (orphan / ".git").mkdir(parents=True)

    outcomes = _sync_and_prune(YamlManifestRepository(), StubGit(), workspace, tmp_path)

    assert {o.repo: o.action for o in outcomes}["svc-old"] == "removed"
    assert not orphan.exists()


@pytest.mark.parametrize(
    ("git", "detail"),
    [
        (
            StubGit(statuses={"svc-old": RepoStatus(branch="main", upstream="o/main", modified=1)}),
            "unsafe local state: dirty working tree",
        ),
        (
            StubGit(
                prune_blockers={
                    "svc-old": ("local commits not reachable from any remote-tracking ref",)
                }
            ),
            "unsafe local state: local commits not reachable from any remote-tracking ref",
        ),
        (
            StubGit(
                prune_blockers={
                    "svc-old": ("dirty working tree", "stash entries present", "local commits")
                }
            ),
            "unsafe local state: dirty working tree; +2 more",
        ),
        (StubGit(prune_fail={"svc-old"}), "not a usable git repo"),
    ],
)
def test_prune_keeps_unsafe_orphan(tmp_path: Path, git: StubGit, detail: str) -> None:
    workspace = _seed_workspace(tmp_path, WorkspaceManifest(repos=[]))
    orphan = workspace.path / "svc-old"
    (orphan / ".git").mkdir(parents=True)

    outcomes = _sync_and_prune(YamlManifestRepository(), git, workspace, tmp_path)

    assert outcomes[0].action == "skipped"
    assert detail in outcomes[0].detail
    assert orphan.exists()


def test_prune_skips_symlinked_orphan(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path, WorkspaceManifest(repos=[]))
    target = tmp_path / "outside"
    (target / ".git").mkdir(parents=True)
    link = workspace.path / "linked"
    link.symlink_to(target, target_is_directory=True)

    outcomes = _sync_and_prune(YamlManifestRepository(), StubGit(), workspace, tmp_path)

    assert [(o.repo, o.action, o.detail) for o in outcomes] == [
        ("linked", "skipped", "symlinked git repo (refusing to prune)")
    ]
    assert link.is_symlink()
    assert target.is_dir()


def test_prune_ignores_non_git_subdirs_and_missing_workspace_dir(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path, WorkspaceManifest(repos=[]))
    not_a_clone = workspace.path / "not-a-clone"
    not_a_clone.mkdir()
    assert _sync_and_prune(YamlManifestRepository(), StubGit(), workspace, tmp_path) == []
    assert not_a_clone.exists()

    class _ReaderStub:
        def read(self, _path: Path) -> WorkspaceManifest:
            return WorkspaceManifest(repos=[])

        def exists(self, _path: Path) -> bool:
            return True

    missing = Workspace(name="gone", path=tmp_path / "missing")
    assert _sync_and_prune(_ReaderStub(), StubGit(), missing, tmp_path) == []


# ---- bare-cache fetch dedup ----------------------------------------------------


def _two_workspaces(tmp_path: Path) -> tuple[Workspace, Workspace]:
    return _seed_workspace(tmp_path, _SVC_A, "a"), _seed_workspace(tmp_path, _SVC_A, "b")


def _count(git: StubGit, op: str) -> int:
    return _ops(git).count(op)


@pytest.mark.parametrize(("shared", "fetches"), [(True, 1), (False, 2)])
def test_bare_fetch_dedup_is_scoped_to_a_shared_tracker(
    tmp_path: Path, shared: bool, fetches: int
) -> None:
    """Callers sharing a ``BareFetchTracker`` fetch each bare once; without one
    (the default) every call refetches."""
    ws_a, ws_b = _two_workspaces(tmp_path)
    git = StubGit()
    use_case = SyncWorkspace(YamlManifestRepository(), git, fs=_FS, cache_dir=tmp_path)
    tracker = BareFetchTracker() if shared else None
    use_case(ws_a, bare_tracker=tracker)
    use_case(ws_b, bare_tracker=tracker)
    assert _count(git, "ensure_bare") == 2
    assert _count(git, "bare_fetch") == fetches


def test_fresh_bare_clone_marks_tracker_fetched_and_skips_bare_fetch(tmp_path: Path) -> None:
    """A bare repo created by this run is already fresh, so same-URL sibling
    clone jobs must not immediately fetch it again."""

    class FreshThenExistingStub(StubGit):
        bare_path = tmp_path / "cache" / "svc-a.git"

        def ensure_bare(self, url: str, *, cache_dir: Path) -> BareCacheEntry:
            self.events.append(("ensure_bare", url))
            return BareCacheEntry(path=self.bare_path, created=_count(self, "ensure_bare") == 1)

    ws_a, ws_b = _two_workspaces(tmp_path)
    git = FreshThenExistingStub()
    use_case = SyncWorkspace(YamlManifestRepository(), git, fs=_FS, cache_dir=tmp_path)
    tracker = BareFetchTracker()
    use_case(ws_a, bare_tracker=tracker)
    use_case(ws_b, bare_tracker=tracker)

    assert _count(git, "bare_fetch") == 0, git.events
    assert tracker.fetched == {git.bare_path}


def test_bare_fetch_dedup_is_threadsafe(tmp_path: Path) -> None:
    """Concurrent calls sharing a tracker and URL still bare_fetch exactly once;
    the stub sleeps inside ``bare_fetch`` to make the race deterministic."""

    class SlowFetchStub(StubGit):
        def bare_fetch(self, bare_path: Path) -> None:
            time.sleep(0.05)
            super().bare_fetch(bare_path)

    workspaces = [_seed_workspace(tmp_path, _SVC_A, name) for name in "abcd"]
    git = SlowFetchStub()
    use_case = SyncWorkspace(YamlManifestRepository(), git, fs=_FS, cache_dir=tmp_path)
    tracker = BareFetchTracker()
    barrier = threading.Barrier(len(workspaces))

    def run(ws: Workspace) -> list[SyncOutcome]:
        barrier.wait()
        return use_case(ws, bare_tracker=tracker)

    with ThreadPoolExecutor(max_workers=len(workspaces)) as pool:
        list(pool.map(run, workspaces))

    assert _count(git, "bare_fetch") == 1, git.events


def test_bare_fetch_failure_leaves_url_unclaimed_for_retry(tmp_path: Path) -> None:
    """A failed ``bare_fetch`` must not mark the URL fetched, so the next call
    sharing the tracker retries instead of reusing a never-fetched bare."""

    class FlakyFetchStub(StubGit):
        failed = False

        def bare_fetch(self, bare_path: Path) -> None:
            if not self.failed:
                self.failed = True
                raise GitError("transient network failure")
            super().bare_fetch(bare_path)

    ws_a, ws_b = _two_workspaces(tmp_path)
    git = FlakyFetchStub()
    use_case = SyncWorkspace(YamlManifestRepository(), git, fs=_FS, cache_dir=tmp_path)
    tracker = BareFetchTracker()

    first = use_case(ws_a, bare_tracker=tracker)
    second = use_case(ws_b, bare_tracker=tracker)

    assert (first[0].action, first[0].detail) == (
        "failed",
        "cache fetch failed: transient network failure",
    )
    assert second[0].action == "cloned", second
    assert _count(git, "bare_fetch") == 1, git.events


def test_sync_workspace_propagates_non_git_errors(tmp_path: Path) -> None:
    """Non-``GitError`` exceptions (a missing manifest here) propagate instead
    of hiding in an outcome row, so ``report_errors`` surfaces real bugs."""
    ws_path = tmp_path / "broken"
    ws_path.mkdir()
    with pytest.raises(WorkspaceError):
        _sync(StubGit(), Workspace(name="broken", path=ws_path), tmp_path)
