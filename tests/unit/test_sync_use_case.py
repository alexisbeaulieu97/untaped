"""Unit tests for SyncWorkspace, using a stub GitRunner."""

from pathlib import Path

import pytest
from untaped_workspace.application import BareFetchTracker, SyncWorkspace
from untaped_workspace.domain import (
    ManifestDefaults,
    Repo,
    RepoStatus,
    Workspace,
    WorkspaceManifest,
)
from untaped_workspace.errors import GitError, UnmatchedOnlyFilter, WorkspaceError
from untaped_workspace.infrastructure import LocalFilesystem, ManifestRepository

from conftest import StubGit

_FS = LocalFilesystem()


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
    git = StubGit()
    outcomes = SyncWorkspace(ManifestRepository(), git, fs=_FS, cache_dir=tmp_path)(workspace)
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
    git = StubGit()
    SyncWorkspace(ManifestRepository(), git, fs=_FS, cache_dir=tmp_path)(workspace)
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
    git = StubGit()
    SyncWorkspace(ManifestRepository(), git, fs=_FS, cache_dir=tmp_path)(workspace)
    clone_event = next(e for e in git.events if e[0] == "clone")
    assert clone_event[2] == "feature/x"


def test_skips_dirty_existing_repo(tmp_path: Path) -> None:
    workspace = _seed_workspace(
        tmp_path,
        WorkspaceManifest(repos=[Repo(url="https://x/svc-a.git")]),
    )
    (workspace.path / "svc-a").mkdir()
    git = StubGit(
        on_disk=["svc-a"],
        statuses={"svc-a": RepoStatus(branch="main", modified=2)},
    )
    outcomes = SyncWorkspace(ManifestRepository(), git, fs=_FS, cache_dir=tmp_path)(workspace)
    assert outcomes[0].action == "skip"
    assert "dirty" in outcomes[0].detail


def test_skips_diverged_repo(tmp_path: Path) -> None:
    workspace = _seed_workspace(
        tmp_path,
        WorkspaceManifest(repos=[Repo(url="https://x/svc-a.git")]),
    )
    (workspace.path / "svc-a").mkdir()
    git = StubGit(
        on_disk=["svc-a"],
        statuses={"svc-a": RepoStatus(branch="main", ahead=2, behind=3)},
    )
    outcomes = SyncWorkspace(ManifestRepository(), git, fs=_FS, cache_dir=tmp_path)(workspace)
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
    git = StubGit(
        on_disk=["svc-a"],
        statuses={"svc-a": RepoStatus(branch="feature/x")},
    )
    outcomes = SyncWorkspace(ManifestRepository(), git, fs=_FS, cache_dir=tmp_path)(workspace)
    assert outcomes[0].action == "skip"
    assert "expected main" in outcomes[0].detail


def test_pulls_when_behind_clean(tmp_path: Path) -> None:
    workspace = _seed_workspace(
        tmp_path,
        WorkspaceManifest(repos=[Repo(url="https://x/svc-a.git")]),
    )
    (workspace.path / "svc-a").mkdir()
    git = StubGit(
        on_disk=["svc-a"],
        statuses={"svc-a": RepoStatus(branch="main", behind=3)},
    )
    outcomes = SyncWorkspace(ManifestRepository(), git, fs=_FS, cache_dir=tmp_path)(workspace)
    assert outcomes[0].action == "pull"
    assert "3 commits" in outcomes[0].detail
    assert ("pull", "svc-a", "main") in git.events


def test_up_to_date(tmp_path: Path) -> None:
    workspace = _seed_workspace(
        tmp_path,
        WorkspaceManifest(repos=[Repo(url="https://x/svc-a.git")]),
    )
    (workspace.path / "svc-a").mkdir()
    git = StubGit(
        on_disk=["svc-a"],
        statuses={"svc-a": RepoStatus(branch="main")},
    )
    outcomes = SyncWorkspace(ManifestRepository(), git, fs=_FS, cache_dir=tmp_path)(workspace)
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
    git = StubGit()
    outcomes = SyncWorkspace(ManifestRepository(), git, fs=_FS, cache_dir=tmp_path)(
        workspace, only=["svc-b"]
    )
    assert [o.repo for o in outcomes] == ["svc-b"]


def test_only_rejects_unknown_identifier(tmp_path: Path) -> None:
    """Strict mode (default) raises :class:`UnmatchedOnlyFilter` carrying
    the unmatched identifiers as a typed field — not a bare
    :class:`WorkspaceError` with stringly-typed contents. Lets future
    callers ``except`` precisely without parsing the error message."""
    workspace = _seed_workspace(
        tmp_path,
        WorkspaceManifest(
            repos=[
                Repo(url="https://x/svc-a.git"),
                Repo(url="https://x/svc-b.git"),
            ],
        ),
    )
    git = StubGit()
    with pytest.raises(UnmatchedOnlyFilter) as excinfo:
        SyncWorkspace(ManifestRepository(), git, fs=_FS, cache_dir=tmp_path)(
            workspace, only=["svc-b", "typo", "also-typo"]
        )
    assert excinfo.value.unmatched == ("also-typo", "typo")
    # The exception text still includes the identifiers (for the
    # default ``report_errors`` stderr surface).
    msg = str(excinfo.value)
    assert "typo" in msg
    assert "also-typo" in msg
    # And no git work should have happened.
    assert git.events == []
    # Backward compat: still subclasses WorkspaceError so existing
    # report_errors() catch sites continue to work.
    assert isinstance(excinfo.value, WorkspaceError)


def test_only_unmatched_under_strict_false_yields_per_identifier_rows(
    tmp_path: Path,
) -> None:
    """Under ``strict_only=False``, every unmatched ``--only`` identifier
    becomes its own ``unmatched`` outcome row — not a single synthetic
    ``repo="<all>"`` sentinel. The discriminator lives in the type-safe
    ``action`` Literal and the typo lives in ``repo`` where downstream
    consumers (``awk``, ``cut``, ``jq``) can read it."""
    workspace = _seed_workspace(
        tmp_path,
        WorkspaceManifest(
            repos=[Repo(url="https://x/svc-a.git"), Repo(url="https://x/svc-b.git")],
        ),
    )
    git = StubGit()
    outcomes = SyncWorkspace(ManifestRepository(), git, fs=_FS, cache_dir=tmp_path)(
        workspace, only=["nonexistent", "also-typo"], strict_only=False
    )
    actions = [(o.repo, o.action) for o in outcomes]
    assert actions == [
        ("also-typo", "unmatched"),
        ("nonexistent", "unmatched"),
    ]
    assert all("not in this workspace's manifest" in o.detail for o in outcomes)
    # No git work should have happened.
    assert git.events == []


def test_only_partial_match_under_strict_false_emits_unmatched_rows(
    tmp_path: Path,
) -> None:
    """Partial-miss must surface unmatched identifiers — typos shouldn't
    be silent just because a sibling ``--only`` value happened to
    match. Previous behaviour silently swallowed the typo whenever any
    other identifier matched, which masked typos under ``--all``."""
    workspace = _seed_workspace(
        tmp_path,
        WorkspaceManifest(
            repos=[Repo(url="https://x/svc-a.git"), Repo(url="https://x/svc-b.git")],
        ),
    )
    git = StubGit()
    outcomes = SyncWorkspace(ManifestRepository(), git, fs=_FS, cache_dir=tmp_path)(
        workspace, only=["svc-a", "nonexistent"], strict_only=False
    )
    by_repo = {o.repo: o.action for o in outcomes}
    assert by_repo == {
        "nonexistent": "unmatched",
        "svc-a": "clone",
    }


def test_only_unknown_default_strict_raises_typed_exception(tmp_path: Path) -> None:
    """``strict_only=True`` is the default and preserves single-workspace
    strictness; raises :class:`UnmatchedOnlyFilter` (not bare
    :class:`WorkspaceError`) so callers can react precisely on the
    typed field rather than parsing the error message."""
    workspace = _seed_workspace(
        tmp_path,
        WorkspaceManifest(repos=[Repo(url="https://x/svc-a.git")]),
    )
    git = StubGit()
    with pytest.raises(UnmatchedOnlyFilter) as excinfo:
        SyncWorkspace(ManifestRepository(), git, fs=_FS, cache_dir=tmp_path)(
            workspace, only=["typo"]
        )
    assert excinfo.value.unmatched == ("typo",)


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

    git = StubGit(
        on_disk=["svc-a", "svc-old"],
        statuses={
            "svc-a": RepoStatus(branch="main"),
            "svc-old": RepoStatus(branch="main"),
        },
    )
    outcomes = SyncWorkspace(ManifestRepository(), git, fs=_FS, cache_dir=tmp_path)(
        workspace, prune=True
    )
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

    git = StubGit(
        on_disk=["svc-old"],
        statuses={"svc-old": RepoStatus(branch="main", modified=1)},
    )
    outcomes = SyncWorkspace(ManifestRepository(), git, fs=_FS, cache_dir=tmp_path)(
        workspace, prune=True
    )
    assert outcomes[0].action == "skip"
    assert orphan.exists()


def test_clone_failure_yields_skip(tmp_path: Path) -> None:
    """``clone_with_reference`` raising ``GitError`` surfaces as a
    ``"clone failed: <git err>"`` row. Pins the uniform ``<step>:
    <error>`` prefixing that the ``_step`` contextmanager guarantees."""
    workspace = _seed_workspace(
        tmp_path,
        WorkspaceManifest(repos=[Repo(url="https://x/svc-a.git")]),
    )
    git = StubGit(clone_fail={"svc-a"})
    outcomes = SyncWorkspace(ManifestRepository(), git, fs=_FS, cache_dir=tmp_path)(workspace)
    assert outcomes[0].action == "skip"
    assert outcomes[0].detail == "clone failed: clone failed"


def test_fetch_failure_yields_skip(tmp_path: Path) -> None:
    """Bare-cache ``bare_fetch`` raising ``GitError`` surfaces as a
    ``"cache fetch failed: <git err>"`` row."""
    workspace = _seed_workspace(
        tmp_path,
        WorkspaceManifest(repos=[Repo(url="https://x/svc-a.git")]),
    )
    git = StubGit(fetch_fail=True)
    outcomes = SyncWorkspace(ManifestRepository(), git, fs=_FS, cache_dir=tmp_path)(workspace)
    assert outcomes[0].action == "skip"
    assert outcomes[0].detail == "cache fetch failed: network down"


def test_ensure_bare_failure_yields_skip(tmp_path: Path) -> None:
    """``ensure_bare`` raising ``GitError`` inside ``_ensure_bare_fresh``
    surfaces under the same ``"cache fetch failed: <git err>"`` prefix
    as ``bare_fetch`` failure — both are bare-cache plumbing from
    ``_sync_repo``'s point of view, so the prefix is shared."""

    class _BareErrorStub(StubGit):
        def ensure_bare(self, url: str, *, cache_dir: Path) -> Path:
            self.events.append(("ensure_bare", url))
            raise GitError("permission denied")

    workspace = _seed_workspace(
        tmp_path,
        WorkspaceManifest(repos=[Repo(url="https://x/svc-a.git")]),
    )
    git = _BareErrorStub()
    outcomes = SyncWorkspace(ManifestRepository(), git, fs=_FS, cache_dir=tmp_path)(workspace)
    assert outcomes[0].action == "skip"
    assert outcomes[0].detail == "cache fetch failed: permission denied"


def test_existing_clone_is_fetched_before_status(tmp_path: Path) -> None:
    """`status.behind` reads `origin/<branch>` from the working clone — that
    ref is stale unless we fetch the clone first."""
    workspace = _seed_workspace(
        tmp_path,
        WorkspaceManifest(repos=[Repo(url="https://x/svc-a.git")]),
    )
    (workspace.path / "svc-a").mkdir()  # existing clone
    git = StubGit(on_disk=["svc-a"])
    SyncWorkspace(ManifestRepository(), git, fs=_FS, cache_dir=tmp_path)(workspace)

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
    git = StubGit()
    SyncWorkspace(ManifestRepository(), git, fs=_FS, cache_dir=tmp_path)(workspace)
    op_names = [event[0] for event in git.events]
    assert "clone" in op_names
    assert "fetch" not in op_names


def test_local_fetch_failure_yields_skip(tmp_path: Path) -> None:
    """A network-flaky `git fetch` on an existing clone is a skip, not abort.
    Surfaces as ``"fetch failed: <git err>"`` — distinct from the
    bare-cache prefix above so log-greppers can tell the two apart."""
    workspace = _seed_workspace(
        tmp_path,
        WorkspaceManifest(repos=[Repo(url="https://x/svc-a.git")]),
    )
    (workspace.path / "svc-a").mkdir()
    git = StubGit(on_disk=["svc-a"], local_fetch_fail={"svc-a"})
    outcomes = SyncWorkspace(ManifestRepository(), git, fs=_FS, cache_dir=tmp_path)(workspace)
    assert outcomes[0].action == "skip"
    assert outcomes[0].detail == "fetch failed: network down"


def test_status_failure_yields_skip(tmp_path: Path) -> None:
    """``status()`` raising during sync surfaces as a
    ``"status failed: <git err>"`` row."""
    workspace = _seed_workspace(
        tmp_path,
        WorkspaceManifest(repos=[Repo(url="https://x/svc-a.git")]),
    )
    (workspace.path / "svc-a").mkdir()
    git = StubGit(on_disk=["svc-a"], status_fail={"svc-a"})
    outcomes = SyncWorkspace(ManifestRepository(), git, fs=_FS, cache_dir=tmp_path)(workspace)
    assert outcomes[0].action == "skip"
    assert outcomes[0].detail == "status failed: status failed"
    assert ("status", "svc-a") in git.events  # the right skip path was taken


def test_detached_head_with_no_target_branch_yields_skip(tmp_path: Path) -> None:
    """Existing clone behind origin with detached HEAD and no manifest target branch → skip."""
    workspace = _seed_workspace(
        tmp_path,
        WorkspaceManifest(repos=[Repo(url="https://x/svc-a.git")]),
    )
    (workspace.path / "svc-a").mkdir()
    git = StubGit(
        on_disk=["svc-a"],
        statuses={"svc-a": RepoStatus(branch=None, behind=3)},
    )
    outcomes = SyncWorkspace(ManifestRepository(), git, fs=_FS, cache_dir=tmp_path)(workspace)
    assert outcomes[0].action == "skip"
    assert "detached head" in outcomes[0].detail


def test_pull_failure_yields_skip(tmp_path: Path) -> None:
    """``ff_only_pull`` raising (e.g. non-fast-forward) surfaces as a
    ``"ff-only pull failed: <git err>"`` row."""
    workspace = _seed_workspace(
        tmp_path,
        WorkspaceManifest(repos=[Repo(url="https://x/svc-a.git")]),
    )
    (workspace.path / "svc-a").mkdir()
    git = StubGit(
        on_disk=["svc-a"],
        statuses={"svc-a": RepoStatus(branch="main", behind=3)},
        pull_fail={"svc-a"},
    )
    outcomes = SyncWorkspace(ManifestRepository(), git, fs=_FS, cache_dir=tmp_path)(workspace)
    assert outcomes[0].action == "skip"
    assert outcomes[0].detail == "ff-only pull failed: non-fast-forward pull"


def test_prune_skipped_when_workspace_dir_missing(tmp_path: Path) -> None:
    """Prune is a no-op when the workspace directory no longer exists on disk."""

    class _ReaderStub:
        def read(self, _path: Path) -> WorkspaceManifest:
            return WorkspaceManifest(repos=[])

        def exists(self, _path: Path) -> bool:
            return True

    missing = tmp_path / "missing"  # never created
    workspace = Workspace(name="prod", path=missing)
    git = StubGit()
    outcomes = SyncWorkspace(_ReaderStub(), git, fs=_FS, cache_dir=tmp_path)(workspace, prune=True)
    assert outcomes == []


def test_prune_skips_non_git_subdir(tmp_path: Path) -> None:
    """Orphan-prune skips subdirs without a ``.git`` — they aren't clones."""
    workspace = _seed_workspace(
        tmp_path,
        WorkspaceManifest(repos=[]),
    )
    not_a_clone = workspace.path / "not-a-clone"
    not_a_clone.mkdir()  # no .git inside
    git = StubGit()
    outcomes = SyncWorkspace(ManifestRepository(), git, fs=_FS, cache_dir=tmp_path)(
        workspace, prune=True
    )
    assert outcomes == []
    assert not_a_clone.exists()  # untouched


def test_prune_status_failure_yields_not_usable_skip(tmp_path: Path) -> None:
    """``status()`` raising during prune → skip with ``not a usable git repo`` (no rmtree)."""
    workspace = _seed_workspace(
        tmp_path,
        WorkspaceManifest(repos=[]),
    )
    orphan = workspace.path / "svc-old"
    orphan.mkdir()
    (orphan / ".git").mkdir()
    git = StubGit(on_disk=["svc-old"], status_fail={"svc-old"})
    outcomes = SyncWorkspace(ManifestRepository(), git, fs=_FS, cache_dir=tmp_path)(
        workspace, prune=True
    )
    assert outcomes[0].action == "skip"
    assert "not a usable git repo" in outcomes[0].detail
    assert orphan.exists()  # not removed


def test_bare_fetch_cached_across_workspaces(tmp_path: Path) -> None:
    """Two ``__call__``s sharing a ``BareFetchTracker`` → bare_fetch runs once."""
    manifest = WorkspaceManifest(repos=[Repo(url="https://x/svc-a.git")])
    ws_a_path = tmp_path / "a"
    ws_b_path = tmp_path / "b"
    ws_a_path.mkdir()
    ws_b_path.mkdir()
    ManifestRepository().write(ws_a_path, manifest)
    ManifestRepository().write(ws_b_path, manifest)
    ws_a = Workspace(name="a", path=ws_a_path)
    ws_b = Workspace(name="b", path=ws_b_path)

    git = StubGit()
    use_case = SyncWorkspace(ManifestRepository(), git, fs=_FS, cache_dir=tmp_path)
    tracker = BareFetchTracker()
    use_case(ws_a, bare_tracker=tracker)
    use_case(ws_b, bare_tracker=tracker)

    ensure_bare_count = sum(1 for e in git.events if e[0] == "ensure_bare")
    bare_fetch_count = sum(1 for e in git.events if e[0] == "bare_fetch")
    assert ensure_bare_count == 2  # lookup happened both times
    assert bare_fetch_count == 1  # fetch deduped via shared cache


def test_no_shared_tracker_means_each_call_refetches(tmp_path: Path) -> None:
    """Default ``bare_tracker=None`` allocates a fresh tracker per call —
    two unrelated single-workspace invocations both fetch. Pins that
    the dedup is now a session contract owned by the caller, not a
    silent instance-state side effect."""
    manifest = WorkspaceManifest(repos=[Repo(url="https://x/svc-a.git")])
    ws_a_path = tmp_path / "a"
    ws_b_path = tmp_path / "b"
    ws_a_path.mkdir()
    ws_b_path.mkdir()
    ManifestRepository().write(ws_a_path, manifest)
    ManifestRepository().write(ws_b_path, manifest)
    ws_a = Workspace(name="a", path=ws_a_path)
    ws_b = Workspace(name="b", path=ws_b_path)

    git = StubGit()
    use_case = SyncWorkspace(ManifestRepository(), git, fs=_FS, cache_dir=tmp_path)
    use_case(ws_a)
    use_case(ws_b)

    bare_fetch_count = sum(1 for e in git.events if e[0] == "bare_fetch")
    assert bare_fetch_count == 2, git.events


def test_bare_fetch_dedup_is_threadsafe(tmp_path: Path) -> None:
    """Concurrent ``__call__`` invocations sharing a ``BareFetchTracker``
    and repo URL must still bare_fetch exactly once. Without per-URL
    locking, the check-and-add window in ``_ensure_bare_fresh`` is
    wide enough for every thread to slip past the membership check
    before any of them adds — we sleep inside the stub's ``bare_fetch``
    to make the race deterministic on a normal CPython runtime."""
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    class SlowFetchStub(StubGit):
        def bare_fetch(self, bare_path: Path) -> None:
            time.sleep(0.05)  # widen the race window so the test is robust
            super().bare_fetch(bare_path)

    manifest = WorkspaceManifest(repos=[Repo(url="https://x/svc-a.git")])
    workspaces: list[Workspace] = []
    for name in ("a", "b", "c", "d"):
        ws_path = tmp_path / name
        ws_path.mkdir()
        ManifestRepository().write(ws_path, manifest)
        workspaces.append(Workspace(name=name, path=ws_path))

    git = SlowFetchStub()
    use_case = SyncWorkspace(ManifestRepository(), git, fs=_FS, cache_dir=tmp_path)
    tracker = BareFetchTracker()

    barrier = threading.Barrier(len(workspaces))

    def run(ws: Workspace) -> list:  # type: ignore[type-arg]
        barrier.wait()
        return use_case(ws, bare_tracker=tracker)

    with ThreadPoolExecutor(max_workers=len(workspaces)) as pool:
        list(pool.map(run, workspaces))

    bare_fetch_count = sum(1 for e in git.events if e[0] == "bare_fetch")
    assert bare_fetch_count == 1, git.events


def test_bare_fetch_failure_leaves_url_unclaimed_for_retry(tmp_path: Path) -> None:
    """If ``bare_fetch`` raises ``GitError``, the URL must stay out of
    the shared cache so a subsequent ``__call__`` (with the same cache)
    retries instead of silently re-using a bare that was never fetched.
    Pins the pre-parallel "retry on failure" semantics that the parallel
    rewrite is documented to preserve (see
    ``packages/untaped-workspace/AGENTS.md``, "sync --all -j N parallelism")."""
    calls = {"n": 0}

    class FlakyFetchStub(StubGit):
        def bare_fetch(self, bare_path: Path) -> None:
            calls["n"] += 1
            if calls["n"] == 1:
                self.events.append(("bare_fetch_failed", bare_path))
                raise GitError("transient network failure")
            super().bare_fetch(bare_path)

    manifest = WorkspaceManifest(repos=[Repo(url="https://x/svc-a.git")])
    ws_a_path = tmp_path / "a"
    ws_b_path = tmp_path / "b"
    ws_a_path.mkdir()
    ws_b_path.mkdir()
    ManifestRepository().write(ws_a_path, manifest)
    ManifestRepository().write(ws_b_path, manifest)

    git = FlakyFetchStub()
    use_case = SyncWorkspace(ManifestRepository(), git, fs=_FS, cache_dir=tmp_path)
    tracker = BareFetchTracker()

    first = use_case(Workspace(name="a", path=ws_a_path), bare_tracker=tracker)
    assert first[0].action == "skip"
    assert first[0].detail == "cache fetch failed: transient network failure"

    # Second call must retry — the URL is unclaimed after the failure.
    second = use_case(Workspace(name="b", path=ws_b_path), bare_tracker=tracker)
    assert second[0].action == "clone", second
    bare_fetch_successes = sum(1 for e in git.events if e[0] == "bare_fetch")
    assert bare_fetch_successes == 1, git.events


def test_sync_workspace_propagates_non_git_errors(tmp_path: Path) -> None:
    """Non-``GitError`` exceptions (e.g. a manifest read failure) must
    propagate out of ``__call__`` rather than being absorbed into a
    ``skip`` row. The CLI's parallel sweep relies on this so a real bug
    surfaces via ``report_errors`` instead of hiding inside one of the
    outcome rows."""
    ws_path = tmp_path / "broken"
    ws_path.mkdir()
    # Don't write a manifest — `ManifestRepository.read` will raise.
    git = StubGit()
    use_case = SyncWorkspace(ManifestRepository(), git, fs=_FS, cache_dir=tmp_path)
    with pytest.raises(WorkspaceError):
        use_case(Workspace(name="broken", path=ws_path))


@pytest.fixture
def _ensure_iterable() -> None:  # placeholder to keep import order tidy
    pass
