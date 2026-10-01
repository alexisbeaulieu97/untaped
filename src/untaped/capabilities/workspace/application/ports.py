"""Workspace port Protocols: git worktrees, the workspace store, and repo resolution."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from contextlib import AbstractContextManager
    from datetime import datetime
    from pathlib import Path

    from untaped.capabilities.workspace.domain.models import (
        ArchivedRecord,
        Checkout,
        CommandResult,
        RepoSpec,
        ResolvedRepo,
        WorkspaceRecord,
        WorktreeStatus,
    )


class GitWorktrees(Protocol):
    """Worktrees checked out from a shared bare cache per repo URL."""

    def checkout(self, url: str, dest: Path, *, branch: str | None, base: str | None) -> Checkout:
        """Add a worktree at ``dest``: on ``branch``, or detached at the base when ``None``."""
        ...

    def status(self, dest: Path, *, branch: str | None, base: str) -> WorktreeStatus | None:
        """Git state of the worktree at ``dest``; ``None`` when it is missing."""
        ...

    def fetch(self, url: str) -> None:
        """Fetch the cache for ``url`` (no-op when it is missing)."""
        ...

    def cache_exists(self, url: str) -> bool: ...

    def remove(self, url: str, dest: Path, *, force: bool) -> None:
        """Remove the worktree at ``dest`` and prune stale worktree entries."""
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
    def archive(self, name: str, *, at: datetime) -> ArchivedRecord: ...
    def locked(self, name: str) -> AbstractContextManager[None]:
        """Serialise ``create``/``add``/``archive`` of workspace ``name`` across processes."""
        ...


class RepoCatalog(Protocol):
    """Resolve a repo identifier to a clone URL."""

    def resolve(self, ident: str) -> ResolvedRepo:
        """Raise ``UsageError`` when ``ident`` is unknown or ambiguous."""
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
