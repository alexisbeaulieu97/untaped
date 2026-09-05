"""User-facing prune safety blocker reasons."""

from __future__ import annotations

DIRTY_WORKTREE_BLOCKER = "dirty working tree"
STASH_BLOCKER = "stash entries present"
UNREACHABLE_COMMITS_BLOCKER = "local commits not reachable from any remote-tracking ref"

__all__ = [
    "DIRTY_WORKTREE_BLOCKER",
    "STASH_BLOCKER",
    "UNREACHABLE_COMMITS_BLOCKER",
]
