"""What a store repo's own files say, read without running git.

The report reads every repo of the store this way (one git run per repo would
be thousands for a large store), and ``release()`` uses the same reading of
who owns each worktree. Only plain config files are read (no includes); refs
are never read from files, since the ref backend is git's choice.
"""

from __future__ import annotations

import os
import re
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

#: Each consumer's private file in a repo it uses: ``untaped-<plugin>.json``.
PRIVATE_PREFIX = "untaped-"
PRIVATE_SUFFIX = ".json"
_ESCAPES = {"n": "\n", "t": "\t", "b": "\b"}
_SPACE = " \t\n\r"


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


def names_admin(dot_git: Path, *admins: Path) -> bool:
    """Whether the worktree file ``dot_git`` names one of ``admins``.

    A worktree removed by hand leaves its entry registered; its path may since
    hold another repository's checkout, which must never be rewritten.
    """
    try:
        text = dot_git.read_text(encoding="utf-8")
    except OSError:
        return False
    named = text.strip().removeprefix("gitdir:").strip()
    if not named:
        return False
    real = os.path.realpath(dot_git.parent / named)
    return any(real == os.path.realpath(admin) for admin in admins)


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


def config_value(
    file: Path, section: str, key: str, *, subsection: str | None = None
) -> str | None:
    """``<section>[.<subsection>].<key>`` from one config file, as ``git config --get`` reads it.

    Only the file itself (no includes); the last value wins. Section and key
    match case-insensitively, a subsection exactly, as in git. ``None`` when
    the file is unreadable or holds no such key.
    """
    try:
        text = file.read_text(encoding="utf-8", errors="replace", newline="")
    except OSError:
        return None
    quoted = "" if subsection is None else rf'\s+"{re.escape(subsection)}"'
    header = re.compile(rf"\[\s*(?i:{re.escape(section)}){quoted}\s*\]\s*(?:[#;].*)?")
    value, inside = None, False
    for line in text.split("\n"):
        stripped = line.strip(_SPACE)
        if stripped.startswith("["):
            inside = header.fullmatch(stripped) is not None
            continue
        name, sep, raw = stripped.partition("=")
        if inside and sep and name.strip(_SPACE).lower() == key.lower():
            value = _config_value(raw)
    return value


def list_repos(root: Path, *, skip: Collection[str] = ()) -> list[Path]:
    """Every ``*.git`` directory under ``root``, sorted; never descends into one.

    Skips symlinks and, at the top level only, hidden directories (names
    starting with ``.``, e.g. scratch dirs) and the names in ``skip``; below
    it a repo may be hidden (``github.com/acme/.github.git``). A missing or
    unreadable directory is skipped. No git runs.
    """
    found: list[Path] = []
    _collect(root, found, top=True, skip=skip)
    return sorted(found)


def _collect(directory: Path, found: list[Path], *, top: bool, skip: Collection[str] = ()) -> None:
    try:
        with os.scandir(directory) as scan:
            entries = list(scan)
    except OSError:
        return
    for entry in entries:
        if top and (entry.name.startswith(".") or entry.name in skip):
            continue
        try:
            if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                continue
        except OSError:
            continue
        path = directory / entry.name
        if entry.name.endswith(".git"):
            found.append(path)
        else:
            _collect(path, found, top=False)


def repo_origin(repo: Path) -> str | None:
    """``remote.origin.url`` from ``<repo>/config`` (``None`` when unset or empty)."""
    return config_value(repo / "config", "remote", "url", subsection="origin") or None


def _config_value(raw: str) -> str:
    """A git config value, parsed as git's ``parse_value`` does.

    Quotes are removed and escapes decoded; a comment ends the value. Outside
    quotes, leading and trailing whitespace is dropped and each inner
    whitespace character becomes a space. Whitespace is ASCII ``" \\t\\n\\r"``
    only, as git's ``isspace``.
    """
    value = ""
    quoted = False
    spaces = 0
    chars = iter(raw)
    for char in chars:
        if not quoted and char in _SPACE:
            spaces += 1 if value else 0
            continue
        if not quoted and char in "#;":
            break
        value += " " * spaces
        spaces = 0
        if char == "\\":
            escaped = next(chars, "")
            value += _ESCAPES.get(escaped, escaped)
        elif char == '"':
            quoted = not quoted
        else:
            value += char
    return value
