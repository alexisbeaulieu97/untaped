"""Workspace port Protocols: git worktrees, the workspace store, and repo resolution."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import TYPE_CHECKING, Protocol

from untaped.sdk import NO_PROGRESS

if TYPE_CHECKING:
    from contextlib import AbstractContextManager
    from datetime import datetime
    from pathlib import Path

    from untaped.sdk import OutcomeRecord, ProgressHandle
    from untaped_workspace.domain.models import (
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

    def checkout(
        self, url: str, dest: Path, *, branch: str | None, base: str | None, lock: str
    ) -> Checkout:
        """Add a worktree at ``dest``, locked for ``lock``: on ``branch``, or detached at base."""
        ...

    def status(self, dest: Path, *, branch: str | None, base: str) -> WorktreeStatus | None:
        """Git state of the worktree at ``dest``; ``None`` when it is missing.

        Commits the ``base`` tag reaches are not unpushed work.
        """
        ...

    def fetch(self, url: str, dest: Path, *, progress: ProgressHandle = NO_PROGRESS) -> str | None:
        """Fetch ``url``'s store repo and refresh ``dest``'s config (no-op when not stored).

        Returns why the history backfill stopped (a warning, not a failure),
        else ``None``. ``progress`` hears the store's phases.
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

    def remove(self, url: str, dest: Path, *, base: str, force: bool) -> None:
        """Remove the worktree at ``dest`` and prune stale worktree entries.

        Unforced, it refuses on work made since the status check (as :meth:`status` counts it).
        """
        ...

    def store_use(self, url: str, *, bases: Collection[str]) -> StoreUse | None:
        """Local branches and workspace worktrees of ``url``'s store repo; ``None``: not stored.

        Commits the tags among ``bases`` reach are not unpushed work.
        """
        ...

    def release(self, url: str, *, branches: Sequence[str]) -> RepoRelease:
        """Release ``url``'s store repo for workspace, deleting the local ``branches`` named."""
        ...

    def drop_worktrees(self, url: str, under: Path) -> list[Path]:
        """Remove workspace's worktrees of ``url``'s store repo at or under ``under`` (forced)."""
        ...

    def repair(self, url: str, worktree: Path) -> bool:
        """Reconnect a moved worktree at ``worktree``; ``False`` when there is none."""
        ...

    def stored_url(self, ident: str) -> str | None:
        """The URL of the store repo keyed ``ident``; ``None`` when the store has none."""
        ...


class WorkspaceStore(Protocol):
    """Workspaces kept in projects: their meta rows, repo rows and holds."""

    def locked(self, record: WorkspaceRecord) -> AbstractContextManager[WorkspaceRecord]:
        """Hold the workspace's project lock; yield its record as stored now."""
        ...

    def all(self) -> list[WorkspaceRecord]: ...
    def at(self, root: Path) -> WorkspaceRecord | None:
        """The workspace of the project at ``root`` exactly, or ``None``."""
        ...

    def start(self, root: Path, name: str, *, at: datetime) -> WorkspaceRecord:
        """Make ``root`` a project and a new workspace; conflict when it is one already."""
        ...

    def adopt(self, record: WorkspaceRecord, *, at: datetime) -> WorkspaceRecord:
        """Make the project a workspace if it isn't one yet."""
        ...

    def hold(self, record: WorkspaceRecord, url: str) -> None:
        """Hold ``url``'s repo for the project."""
        ...

    def put_repos(self, record: WorkspaceRecord, repos: Sequence[RepoSpec]) -> WorkspaceRecord:
        """Write ``repos``' rows, replacing a row for the same folder."""
        ...

    def set_archived(self, record: WorkspaceRecord, at: datetime | None) -> WorkspaceRecord: ...
    def forget(self, record: WorkspaceRecord) -> None:
        """Drop workspace's rows from the project."""
        ...

    def release(
        self, record: WorkspaceRecord, *, force: bool, dry_run: bool
    ) -> list[OutcomeRecord]:
        """Let go of the project's repos through the holds service."""
        ...

    def remove_project(self, record: WorkspaceRecord) -> tuple[str, ...]:
        """Remove the project once it holds nothing; what stays in its folder."""
        ...


class RepoCatalog(Protocol):
    """Resolve what the user typed to a repo, and vet a repo chosen elsewhere."""

    def resolve(self, ident: str, *, progress: ProgressHandle = NO_PROGRESS) -> Repo:
        """Raise ``UsageError`` when ``ident`` is unknown or ambiguous.

        ``progress`` counts the repo sources as they answer.
        """
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
