"""Workspace value objects: records kept in state, inputs, and git observations."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from untaped.capability_api import UtcTimestamp


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


class ResolvedRepo(BaseModel):
    """A repo identifier resolved to a clone URL."""

    model_config = ConfigDict(frozen=True)

    url: str
    name: str
    default_branch: str | None = None


class Checkout(BaseModel):
    """What setting up one worktree did."""

    model_config = ConfigDict(frozen=True)

    action: Literal["created", "checked_out"]
    base: str
    detail: str = ""


class WorktreeStatus(BaseModel):
    """Git state of one worktree; ``unpushed`` counts commits no remote branch has."""

    model_config = ConfigDict(frozen=True)

    branch: str | None
    upstream: str | None
    ahead: int
    behind: int
    modified: int
    untracked: int
    stashed: int
    unpushed: int
