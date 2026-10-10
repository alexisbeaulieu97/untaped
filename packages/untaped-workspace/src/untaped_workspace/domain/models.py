"""Workspace value objects: records kept in state, inputs, and git observations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from untaped.sdk import UtcTimestamp


class RepoSpec(BaseModel):
    """One repo in a workspace: where it comes from and how it is checked out."""

    model_config = ConfigDict(frozen=True)

    url: str
    name: str
    """Display name: ``owner/name`` when known, else derived from the URL."""
    dir: str
    branch: str | None
    """The workspace branch; ``None`` for a read-only (detached) checkout."""
    base: str

    @property
    def read_only(self) -> bool:
        return self.branch is None


class WorkspaceRecord(BaseModel):
    """An active workspace as stored in ``state.yml``."""

    model_config = ConfigDict(frozen=True)

    name: str
    created_at: UtcTimestamp
    repos: tuple[RepoSpec, ...] = ()


class ArchivedRecord(WorkspaceRecord):
    """A workspace after ``archive``: its worktrees are gone, its record stays."""

    archived_at: UtcTimestamp


class RepoArg(BaseModel):
    """One requested repo; ``branch``/``base`` override the workspace defaults."""

    model_config = ConfigDict(frozen=True)

    ident: str
    read_only: bool = False
    branch: str | None = None
    base: str | None = None
    fallback: str | None = None
    """Clone URL to use when ``ident`` cannot be resolved (a piped record's own URL)."""


class ResolvedRepo(BaseModel):
    """A repo identifier resolved to a clone URL."""

    model_config = ConfigDict(frozen=True)

    url: str
    name: str
    default_branch: str | None = None


class Checkout(BaseModel):
    """What setting up one worktree did.

    ``backfill_error`` says why the history backfill after it stopped: the
    worktree is usable, and the next ``status --fetch`` resumes the backfill.
    """

    model_config = ConfigDict(frozen=True)

    action: Literal["created", "checked_out"]
    base: str
    detail: str = ""
    backfill_error: str | None = None


class WorktreeStatus(BaseModel):
    """Git state of one worktree; ``unpushed`` counts commits no remote branch has.

    ``upstream`` is ``None`` until the branch exists on origin; ``submodules``
    is whether any submodule is initialised in the worktree.
    """

    model_config = ConfigDict(frozen=True)

    branch: str | None
    upstream: str | None
    ahead: int
    behind: int
    modified: int
    untracked: int
    stashed: int
    unpushed: int
    submodules: bool = False


@dataclass(frozen=True)
class StoredRepo:
    """A repo of the repo store that workspace has used."""

    key: tuple[str, ...]
    """Its path in the store, a ``store_key``: ``(host, [owner, ...] name.git)``."""
    origin: str
    """The URL it was first fetched from (the store repo's ``remote.origin.url``)."""

    @property
    def ident(self) -> str:
        """``host/owner/name`` (``host/name`` without an owner): the key without ``.git``."""
        return "/".join((*self.key[:-1], self.key[-1].removesuffix(".git")))


@dataclass(frozen=True)
class LocalBranch:
    """A local branch (``refs/heads/*``) of a store repo, as ``remove`` weighs it."""

    name: str
    checked_out: bool
    """Whether a registered worktree has it checked out."""
    unpushed: int
    """Commits on it that no ``refs/remotes/origin/*`` has."""
    stashed: bool
    """Whether a stash entry was made on it."""


@dataclass(frozen=True)
class StoreUse:
    """What a store repo holds that ``remove`` must not lose: branches and live worktrees."""

    branches: tuple[LocalBranch, ...]
    worktrees: int
    """Registered worktrees workspace owns (a ``create`` running meanwhile adds one)."""


@dataclass(frozen=True)
class RepoRelease:
    """What releasing one repo from the store did: ``released`` (kept), or ``removed``."""

    action: Literal["released", "removed"]
    detail: str
    freed_bytes: int = 0


@dataclass(frozen=True)
class CommandResult:
    """What one command run produced; ``returncode`` is ``None`` when it timed out or was cancelled.

    ``cancelled`` means the runner was cancelled before the command started.
    """

    returncode: int | None
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool
    cancelled: bool = False
