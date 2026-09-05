"""Use case: reconcile a workspace's on-disk state with its manifest."""

from __future__ import annotations

import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from untaped_workspace.application.ports import (
    Filesystem,
    GitOperations,
    ManifestReader,
)
from untaped_workspace.application.prune_safety import format_prune_blockers
from untaped_workspace.application.repo_selector import select_repos
from untaped_workspace.domain import (
    Repo,
    SyncAction,
    SyncOutcome,
    Workspace,
    WorkspaceManifest,
)
from untaped_workspace.errors import GitError, UnmatchedRepoFilter


class _Skip(Exception):
    """Module-private control-flow signal carrying a pre-formatted
    ``"<step>: <git err>"`` detail string."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


@contextmanager
def _step(prefix: str) -> Iterator[None]:
    """Catch :class:`GitError` inside the body and re-raise as
    :class:`_Skip` with ``prefix`` joined to the error message via
    ``": "``. Keeps step-chained callers' decision trees linear."""
    try:
        yield
    except GitError as exc:
        raise _Skip(f"{prefix}: {exc}") from exc


@dataclass
class BareFetchTracker:
    """Session-scoped dedup state for bare-cache refreshes.

    Owned by the sweep's orchestrator — :class:`SyncWorkspaces` allocates
    one per ``__call__`` and threads it into every repo job so the same
    bare repo isn't fetched once per workspace or repo. Per-call callers
    that don't need cross-workspace dedup (``add --sync``, ``import
    --sync``) let the singular allocate a fresh tracker via its
    ``bare_tracker=None`` default. ``lock_for(cache_path)`` serialises
    ``ensure_bare → check → fetch → add`` for one bare cache location
    while letting different cache paths proceed in parallel — the
    contract that repo-job parallel sync relies on.
    """

    fetched: set[Path] = field(default_factory=set)
    # init=False so callers can't pass arbitrary locks into the dataclass
    # constructor (the underscore prefix alone is just a convention; the
    # generated __init__ would otherwise still accept them as kwargs).
    # repr=False keeps tracebacks readable when a tracker is referenced.
    _bare_locks: dict[Path, threading.Lock] = field(init=False, repr=False, default_factory=dict)
    _bare_locks_guard: threading.Lock = field(
        init=False, repr=False, default_factory=threading.Lock
    )

    def lock_for(self, bare_path: Path) -> threading.Lock:
        with self._bare_locks_guard:
            return self._bare_locks.setdefault(bare_path, threading.Lock())


class RepoSyncEngine:
    """Per-repo sync state machine shared by sync orchestration entry points."""

    def __init__(
        self,
        git: GitOperations,
        *,
        fs: Filesystem,
        cache_dir: Path,
    ) -> None:
        self._git = git
        self._fs = fs
        self._cache_dir = cache_dir

    def _ensure_bare_fresh(self, url: str, tracker: BareFetchTracker) -> Path:
        # Lock by canonical cache path so URL aliases that share one bare
        # repo do exactly one ensure_bare + bare_fetch.
        bare_lock_path = self._git.bare_cache_path(url, cache_dir=self._cache_dir)
        with tracker.lock_for(bare_lock_path):
            entry = self._git.ensure_bare(url, cache_dir=self._cache_dir)
            bare = entry.path
            if bare not in tracker.fetched:
                if not entry.created:
                    self._git.bare_fetch(bare)
                tracker.fetched.add(bare)
        return bare

    def sync_repo(
        self,
        workspace: Workspace,
        manifest: WorkspaceManifest,
        repo: Repo,
        tracker: BareFetchTracker,
    ) -> SyncOutcome:
        local = workspace.path / repo.name
        target_branch = manifest.target_branch_for(repo)
        try:
            if not self._fs.exists(local):
                with _step("cache fetch failed"):
                    bare = self._ensure_bare_fresh(repo.url, tracker)
                with _step("clone failed"):
                    self._git.clone_with_reference(
                        url=repo.url, dest=local, bare=bare, branch=target_branch
                    )
                return _outcome(
                    workspace, repo, "clone", f"branch {target_branch}" if target_branch else ""
                )

            # Refresh the working clone's remote refs so behind/ahead numbers
            # are current. The bare cache already got ``bare_fetch``, but each
            # working clone keeps its own ``origin/*`` refs.
            with _step("fetch failed"):
                self._git.fetch(local)
            with _step("status failed"):
                status = self._git.status(local)

            if status.dirty:
                return _outcome(workspace, repo, "skip", "dirty working tree")
            if target_branch is not None and status.branch != target_branch:
                return _outcome(
                    workspace,
                    repo,
                    "skip",
                    f"on {status.branch or 'detached'}, expected {target_branch}",
                )
            if status.diverged:
                return _outcome(workspace, repo, "skip", "diverged from origin")
            if status.behind == 0:
                detail = f"{status.ahead} ahead" if status.ahead else "already up to date"
                return _outcome(workspace, repo, "up-to-date", detail)

            target = status.branch or target_branch
            if target is None:
                return _outcome(workspace, repo, "skip", "detached head")
            with _step("ff-only pull failed"):
                self._git.ff_only_pull(local, branch=target)
            return _outcome(workspace, repo, "pull", f"{status.behind} commits")
        except _Skip as exc:
            return _outcome(workspace, repo, "skip", exc.detail)

    def prune_orphans(self, workspace: Workspace, manifest: WorkspaceManifest) -> list[SyncOutcome]:
        if not self._fs.is_dir(workspace.path):
            return []
        declared = {r.name for r in manifest.repos}
        outcomes: list[SyncOutcome] = []
        for entry in self._fs.iterdir(workspace.path):
            if entry.name in declared:
                continue
            if self._fs.is_symlink(entry):
                if self._fs.exists(entry / ".git"):
                    outcomes.append(
                        SyncOutcome(
                            workspace=workspace.name,
                            repo=entry.name,
                            action="skip",
                            detail="symlinked git repo (refusing to prune)",
                        )
                    )
                continue
            if not self._fs.is_dir(entry):
                continue
            if not self._fs.exists(entry / ".git"):
                continue
            outcomes.append(self._prune_orphan(workspace, entry))
        return outcomes

    def _prune_orphan(self, workspace: Workspace, entry: Path) -> SyncOutcome:
        try:
            blockers = self._git.prune_blockers(entry)
        except GitError:
            return SyncOutcome(
                workspace=workspace.name,
                repo=entry.name,
                action="skip",
                detail="not a usable git repo",
            )
        if blockers:
            return SyncOutcome(
                workspace=workspace.name,
                repo=entry.name,
                action="skip",
                detail=format_prune_blockers(blockers),
            )
        self._fs.rmtree(entry)
        return SyncOutcome(
            workspace=workspace.name,
            repo=entry.name,
            action="remove",
            detail="no longer declared",
        )


class SyncWorkspace:
    def __init__(
        self,
        manifests: ManifestReader,
        git: GitOperations,
        *,
        fs: Filesystem,
        cache_dir: Path,
    ) -> None:
        self._manifests = manifests
        self._engine = RepoSyncEngine(git, fs=fs, cache_dir=cache_dir)

    def __call__(
        self,
        workspace: Workspace,
        *,
        only: Sequence[str] | None = None,
        prune: bool = False,
        strict_only: bool = True,
        bare_tracker: BareFetchTracker | None = None,
    ) -> list[SyncOutcome]:
        """Reconcile one workspace with its manifest.

        This remains the serial convenience facade for ``add --sync``,
        ``import --sync``, and tests that exercise the single-workspace
        primitive. Multi-workspace CLI sync uses :class:`SyncWorkspaces`
        for repo-job scheduling.
        """
        tracker = bare_tracker if bare_tracker is not None else BareFetchTracker()
        manifest = self._manifests.read(workspace.path)
        repos, unmatched = select_repos(manifest, only)
        if unmatched and strict_only:
            raise UnmatchedRepoFilter(unmatched)
        outcomes: list[SyncOutcome] = [
            SyncOutcome(
                workspace=workspace.name,
                repo=identifier,
                action="unmatched",
                detail="not in this workspace's manifest",
            )
            for identifier in unmatched
        ]
        outcomes.extend(
            self._engine.sync_repo(workspace, manifest, repo, tracker) for repo in repos
        )
        if prune:
            outcomes.extend(self._engine.prune_orphans(workspace, manifest))
        return outcomes


def _outcome(workspace: Workspace, repo: Repo, action: SyncAction, detail: str = "") -> SyncOutcome:
    return SyncOutcome(
        workspace=workspace.name,
        repo=repo.name,
        action=action,
        detail=detail,
    )
