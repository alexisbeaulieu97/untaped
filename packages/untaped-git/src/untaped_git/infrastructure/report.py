"""The repo store's report data (kind ``git.store``), read from disk alone.

No git runs per repo: a store of a thousand repos reports in milliseconds.
"Used by" comes from each repo's private ``untaped-<plugin>.json`` files and
the ``untaped.owner`` of its worktrees, never from refs.
"""

from __future__ import annotations

import os
import statistics
from collections.abc import Callable
from pathlib import Path

from untaped_git.domain.records import RepoCount, StoreReport
from untaped_git.infrastructure.repo_files import (
    config_value,
    list_caches,
    private_files,
    tree_size,
    worktree_entries,
)


def store_report(root: Path, *, version: str | None) -> StoreReport:
    """What the store under ``root`` holds; ``version`` is the git version shown."""
    repos = list_caches(root)
    packs = [
        _count(repo / "objects" / "pack", lambda name: name.endswith(".pack")) for repo in repos
    ]
    ignored: dict[str, int] = {}
    used_by: dict[str, int] = {}
    exclusive: dict[str, int] = {}
    unowned, held = RepoCount(), RepoCount()
    sizes = [tree_size(repo) for repo in repos]
    for repo, size in zip(repos, sizes, strict=True):
        if config_value(repo / "config", "untaped", "filter") == "ignored":
            host = repo.relative_to(root).parts[0]
            ignored[host] = ignored.get(host, 0) + 1
        owners = {*private_files(repo)}
        worktrees = worktree_entries(repo)
        owners.update(entry.owner for entry in worktrees if entry.owner)
        for owner in owners:
            used_by[owner] = used_by.get(owner, 0) + 1
        if len(owners) == 1:
            (only,) = owners
            exclusive[only] = exclusive.get(only, 0) + size
        if config_value(repo / "config", "untaped", "release") is not None:
            unowned = unowned.add(size)
        elif not owners and not worktrees:
            held = held.add(size)
    return StoreReport(
        store_dir=str(root),
        repos=len(repos),
        size_bytes=sum(sizes),
        used_by=dict(sorted(used_by.items())),
        exclusive_bytes=dict(sorted(exclusive.items())),
        unowned=unowned,
        held_by_branches=held,
        git_version=version,
        packs_median=statistics.median(packs) if packs else 0,
        packs_max=max(packs, default=0),
        loose_objects=sum(_loose(repo) for repo in repos),
        filter_ignored=dict(sorted(ignored.items())),
        gc_log=[str(repo.relative_to(root)) for repo in repos if (repo / "gc.log").is_file()],
    )


def _count(directory: Path, keep: Callable[[str], bool]) -> int:
    try:
        with os.scandir(directory) as scan:
            return sum(1 for entry in scan if keep(entry.name))
    except OSError:
        return 0


def _loose(repo: Path) -> int:
    objects = repo / "objects"
    total = 0
    try:
        with os.scandir(objects) as scan:
            for entry in scan:
                if len(entry.name) == 2 and entry.is_dir(follow_symlinks=False):
                    total += _count(Path(entry.path), lambda _name: True)
    except OSError:
        return 0
    return total
