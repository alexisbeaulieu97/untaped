"""What ``RepoStore.release()`` did: the repo stayed for others, or it went.

A plugin releases a store repo when it no longer needs it. The store deletes
the plugin's own refs, private file and worktrees; the repo itself goes only
when nobody else holds anything in it. :class:`Released` says who kept it,
:class:`Removed` how much went.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from untaped.sdk import plural


def _empty() -> Mapping[str, int]:
    return MappingProxyType({})


@dataclass(frozen=True, slots=True)
class Released:
    """The repo stayed: someone else still holds part of it.

    ``plugins`` maps each other plugin holding refs, a private file or
    worktrees to its worktree count; ``foreign_worktrees`` are worktrees no
    untaped plugin owns (added by hand), which release never removes;
    ``branches`` are local branches (``refs/heads/*``) nobody released, and
    ``stash`` says ``refs/stash`` is there.
    """

    plugins: Mapping[str, int] = field(default_factory=_empty)
    foreign_worktrees: tuple[str, ...] = ()
    branches: tuple[str, ...] = ()
    stash: bool = False

    def kept(self) -> str:
        """Who kept the repo: ``workspace (2 worktrees), ansible; branch fix``."""
        parts = [
            f"{name} ({plural(count, 'worktree')})" if count else name
            for name, count in sorted(self.plugins.items())
        ]
        if self.foreign_worktrees:
            count = plural(len(self.foreign_worktrees), "worktree")
            parts.append(f"{count} not untaped's ({', '.join(self.foreign_worktrees)})")
        if self.branches:
            noun = "branch" if len(self.branches) == 1 else "branches"
            parts.append(f"{noun} {', '.join(self.branches)}")
        if self.stash:
            parts.append("a stash")
        return ", ".join(parts)


@dataclass(frozen=True, slots=True)
class Removed:
    """Nobody else held the repo, so it went: ``freed_bytes`` is what it took on disk."""

    freed_bytes: int = 0
