"""Whether a worktree or a branch can be removed without losing work, and what to do when not.

Archive never discards uncommitted, stashed or unpushed work without
``--force``: a worktree is the only copy of what was not pushed, so every
blocker found here must stop a plain ``archive``. ``remove`` applies the same
rule to the local branches it lets the repo store delete: a branch goes only
when every commit on it is pushed and no stash was made on it, unless the
removal is forced, and never while a worktree has it checked out.
"""

from __future__ import annotations

from collections.abc import Sequence

from untaped.sdk import plural
from untaped_workspace.domain.models import LocalBranch, WorktreeStatus
from untaped_workspace.domain.records import StatusRow

UNCOMMITTED = "uncommitted changes"
NOT_STORED = "repo missing from the repo store; local work cannot be checked"
"""Blocker for a repo the store no longer holds: its worktree's state is unknown."""
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
    by_hand = any(b in (NOT_STORED, SUBMODULES) or b.startswith(UNREADABLE) for _, b in pairs)
    if by_hand or not parts:
        parts.append(_BY_HAND_HINT)
    return "; ".join(parts)


def releasable_branches(branches: Sequence[LocalBranch], *, force: bool) -> list[str]:
    """The local branches a release may delete, by name.

    Never one a worktree has checked out; otherwise only a branch whose
    commits are all pushed and with no stash made on it, or, ``force``d,
    every one. A stash itself (``refs/stash``) is never deleted.
    """
    return [
        branch.name
        for branch in branches
        if not branch.checked_out and (force or (not branch.unpushed and not branch.stashed))
    ]


def unpushed_branch_blocker(branch: LocalBranch) -> str | None:
    """Why releasing would lose ``branch``'s commits; ``None`` when they are all pushed."""
    if not branch.unpushed:
        return None
    return f"branch {branch.name}: {plural(branch.unpushed, 'commit')} not pushed"
