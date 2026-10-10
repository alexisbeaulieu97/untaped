"""Bring a bare repository an older untaped version left into the repo store: :func:`adopt`.

Plugins call it from their ``setup migrate-dirs`` rows, one old repository at
a time. The repository moves under ``git.store_dir`` at its
:func:`~untaped_git.domain.url.store_key`, keeps its objects (no download),
and becomes a store repo: a plugin with its own namespace gets its
``refs/heads/*`` and ``refs/tags/*`` renamed into it (workspace keeps the
plain-clone layout it already had), worktrees that move with it are moved,
every registered worktree is repointed at the new location, the plugin's
worktrees are stamped as its own, and the store's policy is written, which
also moves a 10.x workspace repo's shared ``remote.origin.fetch`` into each
worktree.

When the store already holds the repo, one of the two copies is kept,
whichever arrives first (the overlap rule): a copy "holds work" when it has
local branches, a stash or a worktree with no owner or workspace's (a sweep
worktree can be made again; a 10.x github repo's ``refs/heads`` are github's
own). Both holding work is a conflict and nothing changes. Otherwise the
copy holding work is kept; with neither, the complete one over a shallow one;
still tied, the one already in the store. The other copy's private files
the kept one lacks are copied in without ``fetched_at`` and ``pushed_at``, so
its owner fetches into the kept repo on its next sync; its regenerable
worktrees go with it.

This module is the store's own: it uses :class:`RepoStore`'s internals.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from untaped.sdk import (
    ErrorCategory,
    GitCommandError,
    GitResult,
    UntapedError,
    atomic_write,
    attribution,
    cache_origin,
    run_git,
)
from untaped_git.domain.namespace import PLAIN_CLONE, layout_for
from untaped_git.infrastructure.repo_files import private_file, private_files, worktree_entries
from untaped_git.infrastructure.store import REMOVING_SUFFIX, TIMEOUT, RepoStore, _plugin_name

#: What a copied private file loses, so its owner's next sync fetches.
_FRESHNESS_KEYS = ("fetched_at", "pushed_at")
#: The 10.x workspace layout mark, replaced by the store's ``untaped.store``.
_OLD_LAYOUT_KEY = "untaped.layout"


@dataclass(frozen=True, slots=True)
class Adopted:
    """What :func:`adopt` did with one old repository.

    ``moved``: it is now the store repo at ``repo``. ``replaced``: it moved
    in over a store copy that held no work (that copy's private files were
    kept). ``dropped``: the store's copy was kept and the old repository
    deleted (its private files copied in).
    """

    repo: Path
    action: Literal["moved", "replaced", "dropped"]


def adopt(
    source: Path,
    *,
    plugin: object,
    error: type[UntapedError],
    worktrees: tuple[Path, Path] | None = None,
    owned: Iterable[Path] = (),
) -> Adopted:
    """Move the bare repository ``source`` into the repo store for ``plugin``; see the module.

    ``worktrees`` is ``(old directory, new directory)``: every registered
    worktree inside the old one moves into the new one under its own name and
    is stamped as ``plugin``'s. ``owned`` names other worktrees to stamp as
    ``plugin``'s own, so its release removes them and nobody else's does.
    ``error`` is the plugin's error class. Raises it when ``source`` has no
    ``origin`` URL, or when both it and the store's copy hold work
    (``conflict``, nothing changed).
    """
    name = _plugin_name(plugin)
    label = cache_origin(source)
    if label is None:
        raise error(f"{source} has no origin URL, so it has no place in the repo store")
    store = RepoStore.for_url(label, plugin=plugin, error=error)
    target = store.path
    moves = _worktree_moves(source, worktrees)
    owned = [*owned, *moves.values()]
    with store._locked():
        if _same(source, target):
            action: Literal["moved", "replaced", "dropped"] = "moved"
        elif store.exists():
            action = _overlap(store, source, plugin=name, moves=moves, error=error)
            if action == "dropped":
                return Adopted(target, "dropped")
            _move(source, target, error=error)
        else:
            _move(source, target, error=error)
        _move_worktrees(moves, error=error)
        _repoint(store, moves)
        if name not in PLAIN_CLONE:
            _rename_refs(store, name)
        store._git(["config", "--local", "--unset-all", _OLD_LAYOUT_KEY], check=False)
        stamped = [path for path in owned if path.is_dir()]
        if stamped or worktree_entries(target):
            store._enable_worktree_config()
        for path in stamped:
            store._write_owner_config(path)
        store._ensure()
    return Adopted(target, action)


def _worktree_moves(source: Path, worktrees: tuple[Path, Path] | None) -> dict[Path, Path]:
    """Each registered worktree of ``source`` inside the old directory → its new directory."""
    if worktrees is None:
        return {}
    old_root, new_root = _real(worktrees[0]), worktrees[1]
    moves = {}
    for entry in worktree_entries(source):
        if entry.path is not None and _real(entry.path).is_relative_to(old_root):
            moves[entry.path] = new_root / _real(entry.path).relative_to(old_root)
    return moves


def _overlap(
    store: RepoStore,
    source: Path,
    *,
    plugin: str,
    moves: Mapping[Path, Path],
    error: type[UntapedError],
) -> Literal["replaced", "dropped"]:
    """Settle which copy stays when the store already holds ``source``'s repo."""
    target = store.path
    mirror = plugin not in PLAIN_CLONE
    source_work = _holds_work(source, mirror=mirror, error=error)
    target_work = _holds_work(target, mirror=False, error=error)
    if source_work and target_work:
        raise error(
            f"{source} and the repo store's {target} both hold work (local branches, a stash "
            "or worktrees); nothing changed",
            category=ErrorCategory.CONFLICT,
            hint=(
                "push or remove the branches and worktrees of one copy, or move "
                f"{source} out of the way, then run `untaped setup migrate-dirs` again"
            ),
        )
    keep_source = source_work or (not target_work and _shallow(target) and not _shallow(source))
    if not keep_source:
        _copy_private_files(source, target)
        for old in moves:
            shutil.rmtree(old, ignore_errors=True)
        shutil.rmtree(source, ignore_errors=True)
        return "dropped"
    _copy_private_files(target, source)
    for entry in worktree_entries(target):
        store._remove_worktree(entry)
    removing = target.with_name(target.name + REMOVING_SUFFIX)
    shutil.rmtree(removing, ignore_errors=True)
    try:
        target.rename(removing)
    except OSError as exc:
        raise error(
            f"could not replace repo store directory {target}: {exc.strerror or exc}",
            category=ErrorCategory.FAILED,
            system="local",
        ) from exc
    shutil.rmtree(removing, ignore_errors=True)
    return "replaced"


def _holds_work(repo: Path, *, mirror: bool, error: type[UntapedError]) -> bool:
    """Local branches, a stash or a live worktree holding someone's work.

    ``mirror``: the repo's ``refs/heads`` and unstamped worktrees are a
    plugin's own (a 10.x github repo), never a user's.
    """
    roots = ["refs/stash"] if mirror else ["refs/heads", "refs/stash"]
    refs = _git(repo, ["for-each-ref", "--count=1", "--format=%(refname)", *roots], error=error)
    if refs.text.strip():
        return True
    if mirror:
        return False
    for entry in worktree_entries(repo):
        if entry.path is None or not entry.path.is_dir():
            continue
        if entry.owner is None or entry.owner in PLAIN_CLONE:
            return True
    return False


def _shallow(repo: Path) -> bool:
    return (repo / "shallow").is_file()


def _copy_private_files(loser: Path, winner: Path) -> None:
    """Copy the loser's ``untaped-<plugin>.json`` files the winner lacks, freshness dropped."""
    for plugin in private_files(loser):
        kept = private_file(winner, plugin)
        if kept.exists():
            continue
        try:
            data = json.loads(private_file(loser, plugin).read_text(encoding="utf-8"))
        except OSError, ValueError:
            continue
        if isinstance(data, dict):
            for key in _FRESHNESS_KEYS:
                data.pop(key, None)
        atomic_write(kept, json.dumps(data, sort_keys=True) + "\n")


def _move(source: Path, target: Path, *, error: type[UntapedError]) -> None:
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            source.rename(target)
        except OSError:
            # Another filesystem (a custom git.store_dir): copy, then delete.
            shutil.move(source, target)
    except OSError as exc:
        raise error(
            f"could not move {source} to {target}: {exc.strerror or exc}",
            category=ErrorCategory.FAILED,
            system="local",
        ) from exc


def _move_worktrees(moves: Mapping[Path, Path], *, error: type[UntapedError]) -> None:
    for old, new in moves.items():
        if not old.is_dir() or new.exists():
            continue
        _move(old, new, error=error)


def _repoint(store: RepoStore, moves: Mapping[Path, Path]) -> None:
    """Point every registered worktree and its admin directory at each other again.

    What ``git worktree repair`` does, written directly: the oldest git the
    store supports can't repair a worktree whose repository and directory
    both moved. ``git worktree repair`` then checks the result.
    """
    moved = {_real(old): new for old, new in moves.items()}
    repaired: list[str] = []
    for entry in worktree_entries(store.path):
        if entry.path is None:
            continue
        path = moved.get(_real(entry.path), entry.path)
        dot_git = path / ".git"
        if not dot_git.is_file():
            continue  # gone, or not a worktree any more: git's own prune decides
        admin = Path(os.path.realpath(entry.admin))
        dot_git.write_text(f"gitdir: {admin}\n", encoding="utf-8")
        (entry.admin / "gitdir").write_text(f"{_real(dot_git)}\n", encoding="utf-8")
        repaired.append(str(path))
    if repaired:
        store._git(["worktree", "repair", *repaired], check=False)


def _rename_refs(store: RepoStore, plugin: str) -> None:
    """Move ``refs/heads/*`` and ``refs/tags/*`` into ``plugin``'s namespace, in one transaction."""
    layout = layout_for(plugin)
    listed = store._git(
        ["for-each-ref", "--format=%(objectname) %(refname)", "refs/heads", "refs/tags"],
        capture=True,
    ).text
    lines = []
    for line in listed.splitlines():
        oid, _, ref = line.partition(" ")
        kind, _, name = ref.removeprefix("refs/").partition("/")
        new = layout.absolute(f"{kind}/{name}")
        lines += [f"update {new} {oid}\n", f"delete {ref} {oid}\n"]
    if lines:
        store._git(["update-ref", "--no-deref", "--stdin"], stdin="".join(lines))


def _git(repo: Path, argv: list[str], *, error: type[UntapedError]) -> GitResult:
    """A local git command on a bare repository outside the store."""
    try:
        return run_git(
            argv,
            git_dir=repo,
            cwd=repo,
            timeout=TIMEOUT,
            capture=True,
            ceiling=True,
            batch_ssh=False,
        )
    except GitCommandError as exc:
        raise error(str(exc), **attribution(exc)) from exc


def _same(left: Path, right: Path) -> bool:
    return left.exists() and right.exists() and left.resolve() == right.resolve()


def _real(path: Path) -> Path:
    return Path(os.path.realpath(path))


def remove_if_emptied(root: Path) -> bool:
    """Delete ``root`` once only empty directories and lock files are left in it.

    What an older cache root holds after every repository moved out: its
    ``<repo>.git.lock`` files and the host and owner directories. Never
    looks inside a ``*.git`` directory. Anything
    else (a repository that could not move, a file of the user's) keeps it.
    Returns whether ``root`` is gone.
    """
    if not root.is_dir() or root.is_symlink():
        return not root.exists()
    directories: list[Path] = []
    for directory, dirs, files in os.walk(root):
        here = Path(directory)
        # Never into a repository: an empty refs/ directory there is load-bearing.
        dirs[:] = [name for name in dirs if not name.endswith(".git")]
        directories.append(here)
        for name in files:
            if name.endswith(".git.lock"):
                (here / name).unlink(missing_ok=True)
    for emptied in reversed(directories):
        with contextlib.suppress(OSError):
            emptied.rmdir()  # only when empty
    return not root.exists()
