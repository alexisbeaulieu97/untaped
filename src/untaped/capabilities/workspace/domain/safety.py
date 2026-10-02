"""Whether a worktree can be removed without losing work, and what to do when not."""

from __future__ import annotations

from collections.abc import Sequence

from untaped.capabilities.workspace.domain.models import WorktreeStatus
from untaped.capabilities.workspace.domain.records import StatusRow
from untaped.sdk import plural

UNCOMMITTED = "uncommitted changes"
CACHE_MISSING = "repo cache missing; local work cannot be checked"
"""Blocker for a repo whose cache is gone: its worktree's state is unknown."""
SUBMODULES = "submodules: archive cannot verify or remove them safely"
UNREADABLE = "git state unreadable"
"""Prefix of the blocker for a worktree git cannot read (``UNREADABLE: <reason>``)."""

_LOCAL_WORK_HINT = "commit and push your changes"
_READ_ONLY_HINT = (
    "create a branch for the commits in the read-only repo and push it "
    "(git switch -c NAME && git push -u origin NAME)"
)
_BY_HAND_HINT = "check the repo by hand, then pass --force"
_STASH_SUFFIXES = ("stash entry", "stash entries")


def archive_blockers(status: WorktreeStatus | None) -> tuple[str, ...]:
    """Reasons archiving would lose work; empty when it is safe (or already gone).

    Read-only repos count unpushed commits too: a commit made on a detached
    checkout is reachable from no branch, so archiving would lose it.
    """
    if status is None:
        return ()
    blockers: list[str] = []
    if status.modified or status.untracked:
        blockers.append(UNCOMMITTED)
    if status.stashed:
        blockers.append(plural(status.stashed, "stash entry", "stash entries"))
    if status.unpushed:
        blockers.append(f"{plural(status.unpushed, 'commit')} not pushed")
    if status.submodules:
        blockers.append(SUBMODULES)
    return tuple(blockers)


def archive_hint(blocked: Sequence[StatusRow]) -> str:
    """A non-destructive next step for each kind of blocker in ``blocked``."""
    pairs = [(row, blocker) for row in blocked for blocker in row.blockers]
    parts: list[str] = []
    unpushed = [row for row, b in pairs if b.endswith(" not pushed")]
    if any(b == UNCOMMITTED for _, b in pairs) or any(not row.read_only for row in unpushed):
        parts.append(_LOCAL_WORK_HINT)
    if any(row.read_only for row in unpushed):
        parts.append(_READ_ONLY_HINT)
    stash_branches = dict.fromkeys(
        row.branch or "(no branch)" for row, b in pairs if b.endswith(_STASH_SUFFIXES)
    )
    if stash_branches:
        parts.append(
            f"pop or drop the stashes you made on {', '.join(stash_branches)} "
            "(`git stash list` also shows other workspaces' stashes of this repo: "
            "never drop those)"
        )
    by_hand = any(b in (CACHE_MISSING, SUBMODULES) or b.startswith(UNREADABLE) for _, b in pairs)
    if by_hand or not parts:
        parts.append(_BY_HAND_HINT)
    return "; ".join(parts)
