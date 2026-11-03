"""How the store adds a plugin's worktrees and checks a ref out in one.

Each first prefetches the tip's blobs, so a host that refuses them fails
before anything is written; the worktree's own config is
:mod:`~untaped_git.worktree_config`'s.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from untaped_git import worktree_config
from untaped_git.repo_files import worktree_entries
from untaped_git.store_git import NO_LAZY
from untaped_git.store_prefetch import prefetch

if TYPE_CHECKING:
    from untaped_git.store_git import StoreGit


def add_worktree(
    store: StoreGit,
    path: Path,
    ref: str,
    *,
    branch: str | None = None,
    lock_reason: str | None = None,
) -> None:
    """Add a worktree at ``path`` on ``ref``; :meth:`RepoStore.worktree_add` documents it."""
    if lock_reason is not None:
        worktree_config.check_reason(store, lock_reason)
    with store.locked():
        store.ensure()
        prefetch(store, trees=[ref])
        worktree_config.enable_worktree_config(store)
        local = ref.removeprefix("refs/heads/")
        if branch is not None:
            argv = ["-b", branch, str(path), ref]
        elif ref.startswith("refs/heads/") and store.has_ref(ref):
            argv = [str(path), local]  # resume a branch the repo keeps
        else:
            argv = ["--detach", str(path), ref]
        store.git(["worktree", "add", "--quiet", *argv], env=NO_LAZY)
        worktree_config.write_owner_config(store, path)
        if branch is not None and ref.startswith("refs/remotes/origin/") and store.layout.plain:
            # The refspec that makes origin/<x> a tracking ref exists only now.
            store.git(["branch", "--quiet", f"--set-upstream-to={ref}"], cwd=path)
        if lock_reason is not None:
            worktree_config.lock_worktree(store, path, reason=lock_reason)


def checkout(store: StoreGit, worktree: Path, ref: str) -> None:
    """Check ``ref`` out in one of the repo's worktrees, its blobs prefetched first."""
    with store.locked():
        worktree_config.check_worktree(store, worktree)
        prefetch(store, trees=[ref])
        store.git(["checkout", "--quiet", ref], cwd=worktree, env=NO_LAZY)


def worktrees_under(store: StoreGit, path: Path) -> list[Path]:
    """The plugin's worktrees registered at or under ``path``, sorted."""
    root = Path(os.path.realpath(path.expanduser()))
    return sorted(
        entry.path
        for entry in worktree_entries(store.path)
        if entry.owner == store.plugin
        and entry.path is not None
        and entry.path.is_relative_to(root)
    )
