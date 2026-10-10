"""The repo store's report data (kind ``git.store``), read from disk alone.

No git runs per repo: a store of a thousand repos reports in milliseconds.
The ``untaped git store`` command that renders it lands with the store's
first consumers.
"""

from __future__ import annotations

import os
import statistics
from collections.abc import Callable
from pathlib import Path

from untaped.sdk import list_caches
from untaped_git.domain.records import StoreReport

_UNTAPED_SECTION = "[untaped]"


def store_report(root: Path, *, version: str | None) -> StoreReport:
    """What the store under ``root`` holds; ``version`` is the git version shown."""
    repos = list_caches(root)
    packs = [
        _count(repo / "objects" / "pack", lambda name: name.endswith(".pack")) for repo in repos
    ]
    ignored: dict[str, int] = {}
    for repo in repos:
        if _filter_value(repo) == "ignored":
            host = repo.relative_to(root).parts[0]
            ignored[host] = ignored.get(host, 0) + 1
    return StoreReport(
        store_dir=str(root),
        repos=len(repos),
        size_bytes=sum(_size(repo) for repo in repos),
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


def _size(path: Path) -> int:
    total = 0
    for directory, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(directory, name)).st_size
            except OSError:
                continue
    return total


def _filter_value(repo: Path) -> str | None:
    """``untaped.filter`` from the repo's own config file (the last value wins)."""
    try:
        lines = (repo / "config").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    section, value = "", None
    for raw in lines:
        line = raw.strip()
        if line.startswith("["):
            section = line.lower().replace(" ", "")
            continue
        key, sep, rest = line.partition("=")
        if sep and section == _UNTAPED_SECTION and key.strip().lower() == "filter":
            value = rest.strip()
    return value
