"""Use case: reconcile a workspace's on-disk state with its manifest."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from untaped_workspace.domain import (
    Repo,
    RepoStatus,
    SyncAction,
    SyncOutcome,
    Workspace,
    WorkspaceManifest,
)
from untaped_workspace.errors import GitError

_RmTree = Callable[[Path], None]


class _ManifestReader(Protocol):
    def read(self, workspace_dir: Path) -> WorkspaceManifest: ...


class _Git(Protocol):
    def ensure_bare(self, url: str, *, cache_dir: Path | None = None) -> Path: ...
    def bare_fetch(self, bare_path: Path) -> None: ...
    def clone_with_reference(
        self, *, url: str, dest: Path, bare: Path, branch: str | None = None
    ) -> None: ...
    def status(self, repo_path: Path) -> RepoStatus: ...
    def ff_only_pull(self, repo_path: Path, *, branch: str) -> None: ...


class SyncWorkspace:
    def __init__(
        self,
        manifests: _ManifestReader,
        git: _Git,
        *,
        cache_dir: Path | None = None,
        rmtree: _RmTree = shutil.rmtree,
    ) -> None:
        self._manifests = manifests
        self._git = git
        self._cache_dir = cache_dir
        self._rmtree = rmtree
        # Bare paths whose `bare_fetch` has already run during this use-case
        # invocation chain. Lets `--all` sync N workspaces sharing repo URLs
        # without re-fetching the same bare N times.
        self._fetched: set[Path] = set()

    def __call__(
        self,
        workspace: Workspace,
        *,
        only: list[str] | None = None,
        prune: bool = False,
    ) -> list[SyncOutcome]:
        manifest = self._manifests.read(workspace.path)
        outcomes = [
            self._sync_repo(workspace, manifest, repo)
            for repo in self._select_repos(manifest, only)
        ]
        if prune:
            outcomes.extend(self._prune_orphans(workspace, manifest))
        return outcomes

    # internal -----------------------------------------------------------

    def _select_repos(self, manifest: WorkspaceManifest, only: list[str] | None) -> list[Repo]:
        if not only:
            return list(manifest.repos)
        wanted = set(only)
        return [r for r in manifest.repos if (r.name in wanted) or (r.url in wanted)]

    def _ensure_bare_fresh(self, url: str) -> Path:
        bare = self._git.ensure_bare(url, cache_dir=self._cache_dir)
        if bare not in self._fetched:
            self._git.bare_fetch(bare)
            self._fetched.add(bare)
        return bare

    def _sync_repo(
        self, workspace: Workspace, manifest: WorkspaceManifest, repo: Repo
    ) -> SyncOutcome:
        local = workspace.path / repo.name
        target_branch = manifest.target_branch_for(repo)

        try:
            bare = self._ensure_bare_fresh(repo.url)
        except GitError as exc:
            return _outcome(workspace, repo, "skip", f"cache fetch failed: {exc}")

        if not local.exists():
            try:
                self._git.clone_with_reference(
                    url=repo.url, dest=local, bare=bare, branch=target_branch
                )
            except GitError as exc:
                return _outcome(workspace, repo, "skip", str(exc))
            return _outcome(
                workspace, repo, "clone", f"branch {target_branch}" if target_branch else ""
            )

        try:
            status = self._git.status(local)
        except GitError as exc:
            return _outcome(workspace, repo, "skip", str(exc))

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
        try:
            self._git.ff_only_pull(local, branch=target)
        except GitError as exc:
            return _outcome(workspace, repo, "skip", str(exc))
        return _outcome(workspace, repo, "pull", f"{status.behind} commits")

    def _prune_orphans(
        self, workspace: Workspace, manifest: WorkspaceManifest
    ) -> list[SyncOutcome]:
        if not workspace.path.is_dir():
            return []
        declared = {r.name for r in manifest.repos}
        outcomes: list[SyncOutcome] = []
        for entry in workspace.path.iterdir():
            if not entry.is_dir() or entry.name in declared:
                continue
            if not (entry / ".git").exists():
                continue
            outcomes.append(self._prune_orphan(workspace, entry))
        return outcomes

    def _prune_orphan(self, workspace: Workspace, entry: Path) -> SyncOutcome:
        try:
            status = self._git.status(entry)
        except GitError:
            return SyncOutcome(
                workspace=workspace.name,
                repo=entry.name,
                action="skip",
                detail="not a usable git repo",
            )
        if status.dirty:
            return SyncOutcome(
                workspace=workspace.name,
                repo=entry.name,
                action="skip",
                detail="dirty working tree (refusing to prune)",
            )
        self._rmtree(entry)
        return SyncOutcome(
            workspace=workspace.name,
            repo=entry.name,
            action="remove",
            detail="no longer declared",
        )


def _outcome(workspace: Workspace, repo: Repo, action: SyncAction, detail: str = "") -> SyncOutcome:
    return SyncOutcome(
        workspace=workspace.name,
        repo=repo.name,
        action=action,
        detail=detail,
    )
