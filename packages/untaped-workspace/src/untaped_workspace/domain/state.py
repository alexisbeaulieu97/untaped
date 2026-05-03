"""Value objects describing per-repo git state and sync results."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class RepoStatus(BaseModel):
    """A snapshot of one repo's git state.

    Computed from ``git status --porcelain=v2 --branch`` plus
    ``git rev-list``. Pure data — no I/O.
    """

    model_config = ConfigDict(frozen=True)

    branch: str | None
    """Current local branch name. ``None`` if detached."""

    ahead: int = 0
    """Local commits not on the upstream."""

    behind: int = 0
    """Upstream commits not on the local branch."""

    modified: int = 0
    """Tracked files with uncommitted changes."""

    untracked: int = 0
    """Untracked files (excludes ignored)."""

    @property
    def dirty(self) -> bool:
        return self.modified > 0 or self.untracked > 0

    @property
    def diverged(self) -> bool:
        return self.ahead > 0 and self.behind > 0


SyncAction = Literal["clone", "pull", "skip", "remove", "up-to-date", "ignored"]
"""What `sync` did (or refused to do) for one repo."""


class SyncOutcome(BaseModel):
    """One row of `untaped workspace sync` output."""

    model_config = ConfigDict(frozen=True)

    workspace: str
    repo: str
    action: SyncAction
    detail: str = ""


class StatusEntry(BaseModel):
    """One row of `untaped workspace status` output."""

    model_config = ConfigDict(frozen=True)

    workspace: str
    repo: str
    cloned: bool
    branch: str | None = None
    ahead: int = 0
    behind: int = 0
    modified: int = 0
    untracked: int = 0


class ForeachOutcome(BaseModel):
    """One row of `untaped workspace foreach` output."""

    model_config = ConfigDict(frozen=True)

    workspace: str
    repo: str
    command: str
    returncode: int
    stdout: str
    stderr: str
    duration_s: float = 0.0
