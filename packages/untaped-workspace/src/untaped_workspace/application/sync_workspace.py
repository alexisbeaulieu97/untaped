"""Use case: reconcile a workspace's on-disk state with its manifest."""

from __future__ import annotations

import threading
from pathlib import Path

from untaped_workspace.application.ports import (
    Filesystem,
    GitOperations,
    ManifestReader,
)
from untaped_workspace.domain import (
    Repo,
    SyncAction,
    SyncOutcome,
    Workspace,
    WorkspaceManifest,
)
from untaped_workspace.errors import GitError, UnmatchedOnlyFilter


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
        self._git = git
        self._fs = fs
        self._cache_dir = cache_dir
        # Bare paths whose `bare_fetch` has already run during this use-case
        # invocation chain. Lets `--all` sync N workspaces sharing repo URLs
        # without re-fetching the same bare N times. Per-URL locks make
        # the "ensure_bare → check → fetch → add" sequence atomic for one
        # URL while letting different URLs proceed in parallel — the
        # whole point of `sync --all -j N`.
        # TODO(#11): swap the in-instance cache + lock plumbing for an
        # external cache argument owned by the composition root.
        self._fetched: set[Path] = set()
        self._url_locks: dict[str, threading.Lock] = {}
        self._url_locks_guard = threading.Lock()

    def __call__(
        self,
        workspace: Workspace,
        *,
        only: list[str] | None = None,
        prune: bool = False,
        strict_only: bool = True,
    ) -> list[SyncOutcome]:
        """Reconcile ``workspace`` with its manifest.

        ``strict_only`` controls how unmatched ``--only`` identifiers
        surface:

        - ``True`` (default, single-workspace mode) — raise
          :class:`UnmatchedOnlyFilter` so a typo on
          ``sync --name x --only typo`` is loud.
        - ``False`` (CLI's ``--all`` path) — synthesise a per-identifier
          ``SyncOutcome(action="unmatched", repo=<identifier>, ...)``
          row for each unmatched value and continue with the matched
          repos. Lets ``sync --all --only repo-x`` traverse every
          workspace, syncing ones that have ``repo-x`` and emitting
          visible rows for any typo.
        """
        manifest = self._manifests.read(workspace.path)
        repos, unmatched = self._select_repos(manifest, only)
        if unmatched and strict_only:
            raise UnmatchedOnlyFilter(unmatched)
        # Order: unmatched rows first (so a missing identifier is
        # visible at the top of per-workspace output), then sync rows,
        # then prune.
        outcomes: list[SyncOutcome] = [
            SyncOutcome(
                workspace=workspace.name,
                repo=identifier,
                action="unmatched",
                detail="not in this workspace's manifest",
            )
            for identifier in unmatched
        ]
        outcomes.extend(self._sync_repo(workspace, manifest, repo) for repo in repos)
        if prune:
            outcomes.extend(self._prune_orphans(workspace, manifest))
        return outcomes

    # internal -----------------------------------------------------------

    def _select_repos(
        self, manifest: WorkspaceManifest, only: list[str] | None
    ) -> tuple[list[Repo], tuple[str, ...]]:
        """Partition ``manifest.repos`` against ``--only``.

        Returns ``(matched, unmatched)``. ``unmatched`` is non-empty
        only when ``only`` was passed and contains identifiers that
        don't appear in this manifest. The caller decides how to react
        — strict mode raises :class:`UnmatchedOnlyFilter`; non-strict
        mode synthesises per-identifier ``unmatched`` outcome rows.
        """
        if not only:
            return list(manifest.repos), ()
        wanted = set(only)
        known = {r.name for r in manifest.repos} | {r.url for r in manifest.repos}
        matched = [r for r in manifest.repos if (r.name in wanted) or (r.url in wanted)]
        unmatched = tuple(sorted(wanted - known))
        return matched, unmatched

    def _ensure_bare_fresh(self, url: str) -> Path:
        # Per-URL lock so concurrent same-URL syncs do exactly one
        # ensure_bare + bare_fetch; different URLs proceed in parallel.
        with self._url_locks_guard:
            url_lock = self._url_locks.setdefault(url, threading.Lock())
        with url_lock:
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

        if not self._fs.exists(local):
            try:
                self._git.clone_with_reference(
                    url=repo.url, dest=local, bare=bare, branch=target_branch
                )
            except GitError as exc:
                return _outcome(workspace, repo, "skip", str(exc))
            return _outcome(
                workspace, repo, "clone", f"branch {target_branch}" if target_branch else ""
            )

        # Refresh the working clone's remote refs so behind/ahead numbers are
        # current. The bare cache already got `bare_fetch`, but each working
        # clone keeps its own `origin/*` refs.
        try:
            self._git.fetch(local)
        except GitError as exc:
            return _outcome(workspace, repo, "skip", f"fetch failed: {exc}")

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
        if not self._fs.is_dir(workspace.path):
            return []
        declared = {r.name for r in manifest.repos}
        outcomes: list[SyncOutcome] = []
        for entry in self._fs.iterdir(workspace.path):
            if not self._fs.is_dir(entry) or entry.name in declared:
                continue
            if not self._fs.exists(entry / ".git"):
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
        self._fs.rmtree(entry)
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
