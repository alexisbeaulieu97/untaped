"""Value objects describing per-repo git state and sync results."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

DEFAULT_FOREACH_TIMEOUT = 600.0
"""Default per-repo timeout for ``workspace foreach`` shell commands."""


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


SyncAction = Literal[
    "clone",
    "pull",
    "skip",
    "remove",
    "up-to-date",
    "unmatched",
    "unavailable",
]
"""What ``sync`` did (or refused to do) for one repo.

``unmatched`` is the synthetic action emitted under ``sync --all --repo
<identifier>`` when ``<identifier>`` is not in this workspace's
manifest. The ``repo`` field on those outcomes carries the unmatched
identifier itself, so downstream consumers can filter on
``action == "unmatched"`` and read the typo from ``repo``.

``unavailable`` is the synthetic action emitted under bulk operations
when a registered workspace exists but its manifest cannot be read. The
``repo`` field is empty because no repo was selected or inspected.
"""


class SyncOutcome(BaseModel):
    """One row of `untaped workspace sync` output."""

    model_config = ConfigDict(frozen=True)

    workspace: str
    repo: str
    action: SyncAction
    detail: str = ""


StatusAction = Literal["status", "unavailable"]
"""Status row kind.

``status`` is the normal per-repo git snapshot. ``unavailable`` is a
workspace-level bulk row with an empty ``repo`` when the workspace
manifest could not be read.
"""


class StatusEntry(BaseModel):
    """One row of `untaped workspace status` output."""

    model_config = ConfigDict(frozen=True)

    workspace: str
    repo: str
    action: StatusAction = "status"
    detail: str = ""
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
