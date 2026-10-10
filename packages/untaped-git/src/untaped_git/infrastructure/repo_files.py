"""What a store repo's own files say, read without running git.

The report reads every repo of the store this way (one git run per repo would
be thousands for a large store), and ``release()`` uses the same reading of
who owns each worktree. Only plain config files are read (no includes); refs
are never read from files, since the ref backend is git's choice.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: Each consumer's private file in a repo it uses: ``untaped-<plugin>.json``.
PRIVATE_PREFIX = "untaped-"
PRIVATE_SUFFIX = ".json"


@dataclass(frozen=True, slots=True)
class WorktreeEntry:
    """One registered worktree: its admin directory, checkout and owning plugin."""

    admin: Path
    """``<repo>/worktrees/<id>``."""

    path: Path | None
    """The worktree's directory, from the admin ``gitdir`` file (``None`` when unreadable)."""

    owner: str | None
    """``untaped.owner`` from its ``config.worktree`` when ``untaped.worktree`` there
    names this worktree; ``None`` for a worktree added by hand (git copies the
    file into a worktree added from an owned one, stamp included)."""


def private_file(repo: Path, plugin: str) -> Path:
    return repo / f"{PRIVATE_PREFIX}{plugin}{PRIVATE_SUFFIX}"


def private_files(repo: Path) -> list[str]:
    """The plugins with a private file in ``repo``, sorted."""
    try:
        with os.scandir(repo) as scan:
            names = [entry.name for entry in scan if entry.is_file(follow_symlinks=False)]
    except OSError:
        return []
    return sorted(
        name.removeprefix(PRIVATE_PREFIX).removesuffix(PRIVATE_SUFFIX)
        for name in names
        if name.startswith(PRIVATE_PREFIX)
        and name.endswith(PRIVATE_SUFFIX)
        and len(name) > len(PRIVATE_PREFIX) + len(PRIVATE_SUFFIX)
    )


def worktree_entries(repo: Path) -> list[WorktreeEntry]:
    """Every worktree registered in ``repo`` (``worktrees/*``), sorted by admin directory."""
    try:
        with os.scandir(repo / "worktrees") as scan:
            admins = sorted(Path(e.path) for e in scan if e.is_dir(follow_symlinks=False))
    except OSError:
        return []
    entries = []
    for admin in admins:
        path: Path | None
        try:
            gitdir = (admin / "gitdir").read_text(encoding="utf-8", errors="replace").strip()
            # Relative with git 2.48+ worktree.useRelativePaths, from the admin
            # directory's real path, so symlinks resolve before any ``..``.
            path = Path(os.path.realpath(admin / gitdir)).parent if gitdir else None
        except OSError:
            path = None
        config = admin / "config.worktree"
        owner = config_value(config, "untaped", "owner") or None
        # git copies config.worktree into a worktree added from this one, so an
        # owner counts only where its stamp names this very worktree.
        if owner is not None and not _same(config_value(config, "untaped", "worktree"), path):
            owner = None
        entries.append(WorktreeEntry(admin=admin, path=path, owner=owner))
    return entries


def _same(stamp: str | None, path: Path | None) -> bool:
    if not stamp or path is None:
        return False
    return os.path.realpath(stamp) == os.path.realpath(path)


def tree_size(path: Path) -> int:
    """Bytes of every file under ``path`` (symlinks not followed)."""
    total = 0
    for directory, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(directory, name)).st_size
            except OSError:
                continue
    return total


def config_value(file: Path, section: str, key: str) -> str | None:
    """``<section>.<key>`` from one git config file (the last value wins), else ``None``.

    For a plain ``[section]`` header only (no subsection), which is all the
    store writes for its own keys.
    """
    try:
        lines = file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    header = f"[{section.lower()}]"
    current, value = "", None
    for raw in lines:
        line = raw.strip()
        if line.startswith("["):
            current = line.lower().replace(" ", "").replace("\t", "")
            continue
        name, sep, rest = line.partition("=")
        if sep and current == header and name.strip().lower() == key.lower():
            value = _unquote(rest.strip())
    return value


def _unquote(value: str) -> str:
    for marker in (" #", "\t#", " ;", "\t;"):
        if not value.startswith('"'):
            value = value.split(marker, 1)[0].rstrip()
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    return value
