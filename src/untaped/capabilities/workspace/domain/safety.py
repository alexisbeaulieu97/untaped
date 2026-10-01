"""Whether a worktree can be removed without losing work."""

from __future__ import annotations

from untaped.capabilities.workspace.domain.models import WorktreeStatus
from untaped.capability_api import plural


def archive_blockers(status: WorktreeStatus | None, *, read_only: bool) -> tuple[str, ...]:
    """Reasons archiving would lose work; empty when it is safe (or already gone)."""
    if status is None:
        return ()
    blockers: list[str] = []
    if status.modified or status.untracked:
        blockers.append("uncommitted changes")
    if status.stashed:
        blockers.append(plural(status.stashed, "stash entry", "stash entries"))
    if not read_only and status.unpushed:
        blockers.append(f"{plural(status.unpushed, 'commit')} not pushed")
    return tuple(blockers)
