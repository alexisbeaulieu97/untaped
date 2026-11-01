"""Workspace value objects: records kept in state, inputs, and git observations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, RootModel, field_validator

from untaped.sdk import Key, Project, UtcTimestamp
from untaped_workspace.domain.repo import Repo


class RepoSpec(Repo):
    """One repo of a workspace (its row in the project): the :class:`Repo` it was added from,
    frozen, and how it is checked out.

    The URL never changes after it is added; ``untaped workspace repos
    resolve`` asks the repo's source again.
    """

    dir: Annotated[str, Key]
    """The worktree's folder, relative to the project's: the row's key."""
    branch: str | None
    """The workspace branch; ``None`` for a read-only (detached) checkout."""
    base: str

    @property
    def read_only(self) -> bool:
        return self.branch is None


class WorkspaceMeta(BaseModel):
    """What makes a project a workspace: its row in the project's state.

    ``created_by`` says who made the project: ``workspace`` (``create``, so
    ``remove`` may remove the project too once nothing else is held) or
    ``user`` (an existing project ``add`` was run in).
    """

    model_config = ConfigDict(frozen=True)

    name: Annotated[str, Key]
    created_by: Literal["workspace", "user"]
    created_at: UtcTimestamp
    archived_at: UtcTimestamp | None = None


class WorkspaceRecord(BaseModel):
    """A workspace as a command sees it: a project, its meta row and its repos.

    ``meta`` is ``None`` for a project no workspace command has written to
    yet: it has no repos, and its first ``add`` makes it a workspace.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    project: Project
    meta: WorkspaceMeta | None = None
    repos: tuple[RepoSpec, ...] = ()

    @property
    def name(self) -> str:
        """The workspace's name: its meta row's, else the project's folder name."""
        return self.meta.name if self.meta is not None else self.project.name

    @property
    def root(self) -> Path:
        """The project's folder, where each repo's worktree is (``root / spec.dir``)."""
        return self.project.root

    @property
    def archived(self) -> bool:
        return self.meta is not None and self.meta.archived_at is not None


#: ``user@host:path``, a clone URL without a scheme.
_SCP = re.compile(r"^[^/@\s]+@[^/:\s]+:")


class RepoKey(RootModel[str]):
    """A repo as a project holds it through workspace: its repo store key, ``host/owner/name``.

    A clone URL normalises to it (the https and ssh URLs of a repo are one
    hold), and so does the key with ``.git``.
    """

    @field_validator("root")
    @classmethod
    def _normalised(cls, value: str) -> str:
        from untaped_workspace.domain.naming import store_ident  # noqa: PLC0415 - a cycle

        if "://" in value or _SCP.match(value):
            return store_ident(value)
        return value.removesuffix(".git")


class RepoArg(BaseModel):
    """One requested repo; ``branch``/``base`` override the workspace defaults."""

    model_config = ConfigDict(frozen=True)

    ident: str
    read_only: bool = False
    branch: str | None = None
    base: str | None = None
    repo: Repo | None = None
    """The repo already chosen (a picked item, a piped record); ``ident`` is then its name."""


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
    """Git state of one worktree; ``unpushed`` counts commits no remote branch nor base tag has.

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
    """What releasing one repo from the store did: ``released`` (the repo stayed for
    someone else), ``removed``, or ``kept`` (a workspace worktree still uses it)."""

    action: Literal["released", "removed", "kept"]
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
