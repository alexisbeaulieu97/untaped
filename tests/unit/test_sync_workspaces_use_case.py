"""Unit tests for the plural ``SyncWorkspaces`` repo-job scheduler."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest
from conftest import StubFilesystem, StubGit, StubManifests

from untaped_workspace.application import BareFetchTracker, RepoSyncEngine, SyncWorkspaces
from untaped_workspace.domain import (
    BareCacheEntry,
    Repo,
    SyncAction,
    SyncOutcome,
    Workspace,
    WorkspaceManifest,
)
from untaped_workspace.errors import UnmatchedRepoFilter, WorkspaceError


class _Notify:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self, message: str, *, fraction: float | None = None, new_phase: bool = False
    ) -> None:
        self.messages.append(message)
        self.calls.append(
            {
                "message": message,
                "fraction": fraction,
                "new_phase": new_phase,
            }
        )


class _Engine:
    def __init__(
        self,
        *,
        gates: dict[tuple[str, str], threading.Event] | None = None,
        release_after: dict[tuple[str, str], threading.Event] | None = None,
        raises: dict[tuple[str, str], Exception] | None = None,
        prune_rows: dict[str, list[SyncOutcome]] | None = None,
        release_all: threading.Event | None = None,
    ) -> None:
        self._gates = gates or {}
        self._release_after = release_after or {}
        self._raises = raises or {}
        self._prune_rows = prune_rows or {}
        self._release_all = release_all
        self.calls: list[tuple[str, str, int]] = []
        self.max_active = 0
        self._active = 0
        self._lock = threading.Lock()
        self._started = threading.Condition(self._lock)

    def sync_repo(
        self,
        workspace: Workspace,
        _manifest: WorkspaceManifest,
        repo: Repo,
        tracker: BareFetchTracker,
    ) -> SyncOutcome:
        with self._lock:
            self.calls.append((workspace.name, repo.name, id(tracker)))
            self._active += 1
            self.max_active = max(self.max_active, self._active)
            self._started.notify_all()
        try:
            if self._release_all is not None:
                self._release_all.wait(timeout=2.0)
            gate = self._gates.get((workspace.name, repo.name))
            if gate is not None:
                gate.wait(timeout=2.0)
            error = self._raises.get((workspace.name, repo.name))
            if error is not None:
                raise error
            return _outcome(workspace.name, repo.name)
        finally:
            post = self._release_after.get((workspace.name, repo.name))
            if post is not None:
                post.set()
            with self._lock:
                self._active -= 1

    def prune_orphans(
        self, workspace: Workspace, _manifest: WorkspaceManifest
    ) -> list[SyncOutcome]:
        return list(self._prune_rows.get(workspace.name, []))

    def wait_for_started(self, count: int) -> None:
        with self._started:
            self._started.wait_for(lambda: len(self.calls) >= count, timeout=2.0)


def _ws(tmp_path: Path, name: str) -> Workspace:
    return Workspace(name=name, path=tmp_path / name)


def _manifest(*names: str) -> WorkspaceManifest:
    return WorkspaceManifest(repos=[Repo(url=f"https://x/{name}.git") for name in names])


def _outcome(
    workspace: str, repo: str, action: SyncAction = "up-to-date", detail: str = ""
) -> SyncOutcome:
    return SyncOutcome(workspace=workspace, repo=repo, action=action, detail=detail)


def _scheduler(
    tmp_path: Path,
    manifests: dict[str, WorkspaceManifest],
    engine: _Engine,
    *,
    notify: _Notify | None = None,
) -> tuple[SyncWorkspaces, list[Workspace]]:
    workspaces = [_ws(tmp_path, name) for name in manifests]
    reader = StubManifests({ws.path: manifests[ws.name] for ws in workspaces})
    return SyncWorkspaces(reader, engine, notify=notify), workspaces


def test_serial_jobs_use_manifest_order_and_one_shared_tracker(tmp_path: Path) -> None:
    engine = _Engine()
    use_case, workspaces = _scheduler(tmp_path, {"prod": _manifest("z-repo", "a-repo")}, engine)

    outcomes = use_case(workspaces, parallel=1)

    assert [(o.workspace, o.repo) for o in outcomes] == [
        ("prod", "z-repo"),
        ("prod", "a-repo"),
    ]
    assert [(ws, repo) for ws, repo, _tracker_id in engine.calls] == [
        ("prod", "z-repo"),
        ("prod", "a-repo"),
    ]
    assert len({tracker_id for *_prefix, tracker_id in engine.calls}) == 1


def test_parallel_single_workspace_preserves_manifest_order(tmp_path: Path) -> None:
    z_gate = threading.Event()
    a_gate = threading.Event()
    a_gate.set()
    engine = _Engine(
        gates={
            ("prod", "z-repo"): z_gate,
            ("prod", "a-repo"): a_gate,
        },
        release_after={("prod", "a-repo"): z_gate},
    )
    notify = _Notify()
    use_case, workspaces = _scheduler(
        tmp_path, {"prod": _manifest("z-repo", "a-repo")}, engine, notify=notify
    )

    outcomes = use_case(workspaces, parallel=2)

    assert [(o.workspace, o.repo) for o in outcomes] == [
        ("prod", "z-repo"),
        ("prod", "a-repo"),
    ]
    assert notify.messages == [
        "syncing 2 repos with up to 2 workers",
        "1/2 repos complete",
        "2/2 repos complete",
    ]
    assert notify.calls[0]["new_phase"] is True


def test_parallel_cap_is_global_repo_jobs_not_workspaces(tmp_path: Path) -> None:
    release = threading.Event()
    engine = _Engine(release_all=release)
    use_case, workspaces = _scheduler(
        tmp_path,
        {
            "alpha": _manifest("a1", "a2"),
            "beta": _manifest("b1", "b2"),
        },
        engine,
    )
    result: dict[str, object] = {}

    def run() -> None:
        try:
            result["outcomes"] = use_case(workspaces, parallel=2)
        except Exception as exc:  # pragma: no cover - surfaced by assertion below.
            result["error"] = exc

    thread = threading.Thread(target=run)
    thread.start()
    engine.wait_for_started(2)
    assert engine.max_active == 2
    assert len(engine.calls) == 2

    release.set()
    thread.join(timeout=2.0)
    assert not thread.is_alive()
    assert "error" not in result
    assert len(result["outcomes"]) == 4
    assert engine.max_active <= 2


def test_parallel_phase_order_is_unmatched_sync_then_prune(tmp_path: Path) -> None:
    z_gate = threading.Event()
    a_gate = threading.Event()
    a_gate.set()
    engine = _Engine(
        gates={
            ("prod", "z-repo"): z_gate,
            ("prod", "a-repo"): a_gate,
        },
        release_after={("prod", "a-repo"): z_gate},
        prune_rows={"prod": [_outcome("prod", "old-repo", "remove")]},
    )
    use_case, workspaces = _scheduler(tmp_path, {"prod": _manifest("z-repo", "a-repo")}, engine)

    outcomes = use_case(
        workspaces,
        only=["ghost", "z-repo", "a-repo"],
        strict_only=False,
        prune=True,
        parallel=2,
    )

    assert [(o.repo, o.action) for o in outcomes] == [
        ("ghost", "unmatched"),
        ("z-repo", "up-to-date"),
        ("a-repo", "up-to-date"),
        ("old-repo", "remove"),
    ]


def test_strict_unmatched_raises_before_network_work(tmp_path: Path) -> None:
    engine = _Engine()
    use_case, workspaces = _scheduler(tmp_path, {"prod": _manifest("api")}, engine)

    with pytest.raises(UnmatchedRepoFilter) as excinfo:
        use_case(workspaces, only=["ghost"], parallel=2)

    assert excinfo.value.unmatched == ("ghost",)
    assert engine.calls == []


def test_caller_supplied_tracker_is_used_for_every_job(tmp_path: Path) -> None:
    engine = _Engine()
    use_case, workspaces = _scheduler(
        tmp_path,
        {
            "alpha": _manifest("api"),
            "beta": _manifest("api"),
        },
        engine,
    )
    tracker = BareFetchTracker()

    use_case(workspaces, parallel=2, bare_tracker=tracker)

    assert {tracker_id for *_prefix, tracker_id in engine.calls} == {id(tracker)}


def test_unexpected_job_exceptions_drain_pool_then_raise(tmp_path: Path) -> None:
    engine = _Engine(raises={("prod", "bad"): RuntimeError("boom")})
    use_case, workspaces = _scheduler(tmp_path, {"prod": _manifest("bad", "good")}, engine)

    with pytest.raises(WorkspaceError) as excinfo:
        use_case(workspaces, parallel=2)

    assert [(ws, repo) for ws, repo, _tracker_id in engine.calls] == [
        ("prod", "bad"),
        ("prod", "good"),
    ]
    message = str(excinfo.value)
    assert "sync failed with unexpected error" in message
    assert "prod/bad: RuntimeError: boom" in message


def test_serial_unexpected_job_exceptions_drain_then_raise(tmp_path: Path) -> None:
    engine = _Engine(raises={("prod", "bad"): RuntimeError("boom")})
    use_case, workspaces = _scheduler(tmp_path, {"prod": _manifest("bad", "good")}, engine)

    with pytest.raises(WorkspaceError) as excinfo:
        use_case(workspaces, parallel=1)

    assert [(ws, repo) for ws, repo, _tracker_id in engine.calls] == [
        ("prod", "bad"),
        ("prod", "good"),
    ]
    message = str(excinfo.value)
    assert "sync failed with unexpected error" in message
    assert "prod/bad: RuntimeError: boom" in message


def test_parallel_same_cache_path_urls_share_bare_fetch_lock(tmp_path: Path) -> None:
    class SameCachePathGit(StubGit):
        def __init__(self) -> None:
            super().__init__()
            self.bare = tmp_path / "cache" / "github.com" / "acme" / "svc.git"

        def bare_cache_path(self, url: str, *, cache_dir: Path) -> Path:
            self.events.append(("bare_cache_path", url))
            return self.bare

        def ensure_bare(self, url: str, *, cache_dir: Path) -> BareCacheEntry:
            self.events.append(("ensure_bare", url))
            return BareCacheEntry(path=self.bare, created=False)

        def bare_fetch(self, bare_path: Path) -> None:
            self.events.append(("bare_fetch", bare_path))
            time.sleep(0.05)

    workspaces = [_ws(tmp_path, "alpha"), _ws(tmp_path, "beta")]
    manifests = StubManifests(
        {
            workspaces[0].path: WorkspaceManifest(
                repos=[
                    Repo(
                        url="https://github.com/acme/svc.git",
                        name="svc-https",
                    )
                ]
            ),
            workspaces[1].path: WorkspaceManifest(
                repos=[
                    Repo(
                        url="git@github.com:acme/svc.git",
                        name="svc-ssh",
                    )
                ]
            ),
        }
    )
    git = SameCachePathGit()
    engine = RepoSyncEngine(git, fs=StubFilesystem(), cache_dir=tmp_path / "cache")

    outcomes = SyncWorkspaces(manifests, engine)(workspaces, parallel=2)

    assert [o.action for o in outcomes] == ["clone", "clone"]
    bare_fetch_count = sum(1 for event in git.events if event[0] == "bare_fetch")
    assert bare_fetch_count == 1, git.events


def test_empty_workspace_list_is_a_noop(tmp_path: Path) -> None:
    engine = _Engine()
    notify = _Notify()
    use_case, workspaces = _scheduler(tmp_path, {}, engine, notify=notify)

    outcomes = use_case(workspaces, parallel=4)

    assert outcomes == []
    assert engine.calls == []
    assert notify.messages == []
