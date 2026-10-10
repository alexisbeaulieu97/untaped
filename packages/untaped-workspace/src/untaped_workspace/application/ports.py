"""Workspace port Protocols: git worktrees, the workspace store, and repo resolution."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from contextlib import AbstractContextManager
    from datetime import datetime
    from pathlib import Path

    from untaped_workspace.domain.models import (
        ArchivedRecord,
        Checkout,
        CommandResult,
        RepoRelease,
        RepoSpec,
        StoredRepo,
        StoreUse,
        WorkspaceRecord,
        WorktreeStatus,
    )
    from untaped_workspace.domain.repo import Repo


class GitWorktrees(Protocol):
    """Worktrees checked out from the repo store, one store repo per repo URL."""

    def checkout(self, url: str, dest: Path, *, branch: str | None, base: str | None) -> Checkout:
        """Add a worktree at ``dest``: on ``branch``, or detached at the base when ``None``."""
        ...

    def status(self, dest: Path, *, branch: str | None) -> WorktreeStatus | None:
        """Git state of the worktree at ``dest``; ``None`` when it is missing."""
        ...

    def fetch(self, url: str, dest: Path) -> str | None:
        """Fetch ``url``'s store repo and refresh ``dest``'s config (no-op when not stored).

        Returns why the history backfill stopped (a warning, not a failure),
        else ``None``.
        """
        ...

    def configure(self, url: str, dest: Path) -> None:
        """Rewrite ``dest``'s worktree config for ``url`` (a no-op when either is missing)."""
        ...

    def in_store(self, url: str) -> bool:
        """Whether the repo store holds ``url``'s repo."""
        ...

    def remote_branches(self, url: str) -> list[str]:
        """Branch names of ``url``'s store repo as last fetched, sorted; ``[]`` when not stored."""
        ...

    def stored_repos(self) -> list[StoredRepo]:
        """Every store repo workspace has used, sorted by :attr:`StoredRepo.ident`."""
        ...

    def remove(self, url: str, dest: Path, *, force: bool) -> None:
        """Remove the worktree at ``dest`` and prune stale worktree entries."""
        ...

    def store_use(self, url: str) -> StoreUse | None:
        """Local branches and workspace worktrees of ``url``'s store repo; ``None``: not stored."""
        ...

    def release(self, url: str, *, branches: Sequence[str]) -> RepoRelease:
        """Release ``url``'s store repo for workspace, deleting the local ``branches`` named."""
        ...


class WorkspaceStore(Protocol):
    """Active and archived workspace records."""

    def active(self) -> list[WorkspaceRecord]: ...
    def archived(self) -> list[ArchivedRecord]: ...
    def get(self, name: str) -> WorkspaceRecord | None: ...
    def create(self, record: WorkspaceRecord) -> None:
        """Store a new active workspace; conflict if the name is active."""
        ...

    def add_repos(self, name: str, repos: Sequence[RepoSpec]) -> WorkspaceRecord: ...
    def update_repos(self, name: str, repos: Sequence[RepoSpec]) -> WorkspaceRecord:
        """Replace the active workspace's repos that share a ``dir`` with one of ``repos``."""
        ...

    def archive(self, name: str, *, at: datetime) -> ArchivedRecord: ...
    def remove(self, name: str) -> list[WorkspaceRecord]:
        """Drop every record named ``name``, active and archived; the records dropped."""
        ...

    def locked(self, name: str) -> AbstractContextManager[None]:
        """Serialise ``create``/``add``/``archive``/``remove`` of workspace ``name``."""
        ...


class RepoCatalog(Protocol):
    """Resolve what the user typed to a repo, and vet a repo chosen elsewhere."""

    def resolve(self, ident: str) -> Repo:
        """Raise ``UsageError`` when ``ident`` is unknown or ambiguous."""
        ...

    def admit(self, repo: Repo) -> Repo:
        """``repo`` (picked or piped), unless its source vouches for another host."""
        ...

    def reask(self, repo: Repo) -> Repo | None:
        """``repo`` as its source lists it now; ``None`` when no source can be asked.

        No source: a typed URL, or a source plugin that is gone or not ready.
        """
        ...


class CommandRunner(Protocol):
    """Run one command in a directory."""

    def run(
        self, argv: Sequence[str], *, cwd: Path, env: Mapping[str, str], timeout: float
    ) -> CommandResult:
        """Run ``argv`` with ``env`` added to the caller's environment; failure is a result."""
        ...

    def cancel(self) -> None:
        """Stop every command still running (an interrupted ``run``)."""
        ...
