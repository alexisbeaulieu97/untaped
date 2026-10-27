"""How a plugin lets go of a store repo: :meth:`RepoStore.release` and its recovery.

``release`` writes the ``untaped.release`` mark, deletes the plugin's refs,
private file and worktrees, then keeps the repo while anyone else holds it or
renames it aside and deletes it. The next fetch or prefetch finishes a release
that was interrupted from the mark. This module is the store's own: it uses
:class:`RepoStore`'s internals.
"""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.sdk import ErrorCategory, dir_bytes, shown_path
from untaped_git.namespace import ORIGIN_HEAD, check_names, is_glob, layout_for
from untaped_git.release import Released, Removed
from untaped_git.repo_files import (
    WorktreeEntry,
    private_file,
    private_files,
    worktree_entries,
)

if TYPE_CHECKING:
    from untaped_git.store import RepoStore

#: The mark ``release()`` writes before deleting anything; ``_ensure()`` finishes from it.
RELEASE_MARK = "untaped.release"
#: Where a repo nobody holds goes, atomically, before it is deleted.
REMOVING_SUFFIX = ".removing"
#: Refs that are some user's data: a repo holding any stays on release.
_HELD_ROOTS = ("refs/untaped/", "refs/remotes/", "refs/heads/", "refs/tags/", "refs/stash")


def release(
    store: RepoStore, *, branches: Sequence[str] = (), unless_in_use: bool = False
) -> Released | Removed:
    """Release ``store``'s plugin from the repo; :meth:`RepoStore.release` documents it."""
    refs = []
    for name in branches:
        reason = check_names([name])
        if reason is None and is_glob(name):
            reason = f"{name!r} is a glob; release takes exact branch names"
        if reason is not None:
            raise store._error(reason, category=ErrorCategory.INVALID)
        refs.append(f"refs/heads/{name}")
    with store._locked():
        if not store.exists():
            remove_removing(store)
            return Removed()
        if unless_in_use and any(
            entry.owner == store._plugin and entry.path is not None and entry.path.is_dir()
            for entry in worktree_entries(store._path)
        ):
            return _holders(store) or Released()
        busy = set(_checked_out(store)) & set(refs)
        if busy:
            names = ", ".join(sorted(ref.removeprefix("refs/heads/") for ref in busy))
            raise store._error(
                f"cannot release {store._path}: a worktree has {names} checked out",
                category=ErrorCategory.CONFLICT,
            )
        marked = store._config_get(RELEASE_MARK)
        if marked is not None and marked != store._plugin:
            # Another plugin's interrupted release: finish it before the mark is reused.
            finish_release(store, marked, ())
        store._git(["config", RELEASE_MARK, store._plugin])
        finish_release(store, store._plugin, refs)
        return settle(store)


def finish_release(store: RepoStore, plugin: str, branches: Sequence[str]) -> None:
    """Delete ``plugin``'s refs, private file and worktrees, and ``branches``."""
    layout = layout_for(plugin)
    refs = store._git(
        ["for-each-ref", "--format=%(refname)", *layout.roots], capture=True
    ).text.split()
    if layout.heads == "refs/remotes/origin/":
        refs.append(ORIGIN_HEAD)
    store._delete(dict.fromkeys([*refs, *branches]))
    private_file(store._path, plugin).unlink(missing_ok=True)
    for entry in worktree_entries(store._path):
        if entry.owner == plugin:
            remove_worktree(store, entry)
    store._git(["worktree", "prune"], check=False)


def remove_worktree(store: RepoStore, entry: WorktreeEntry) -> None:
    if entry.path is not None:
        removed = store._git(
            ["worktree", "remove", "--force", "--force", str(entry.path)], check=False
        )
        if removed.returncode == 0:
            return
        shutil.rmtree(entry.path, ignore_errors=True)
    shutil.rmtree(entry.admin, ignore_errors=True)


def settle(store: RepoStore) -> Released | Removed:
    """Clear the mark when someone still holds the repo, else remove it."""
    released = _holders(store)
    if released is not None:
        store._git(["config", "--unset-all", RELEASE_MARK], check=False)
        return released
    freed = dir_bytes(store._path)
    removing = _removing_path(store)
    remove_removing(store)
    try:
        store._path.rename(removing)
    except OSError as exc:
        raise store._error(
            f"could not remove repo store directory {store._path}: {exc.strerror or exc}",
            category=ErrorCategory.FAILED,
            system="local",
        ) from exc
    shutil.rmtree(removing, ignore_errors=True)
    return Removed(freed)


def _holders(store: RepoStore) -> Released | None:
    """Who still holds the repo, or ``None`` when nobody does."""
    refs = store._git(
        ["for-each-ref", "--format=%(refname)", *_HELD_ROOTS], capture=True
    ).text.split()
    plugins: dict[str, int] = dict.fromkeys(private_files(store._path), 0)
    branches, stash = [], False
    for ref in refs:
        if ref == ORIGIN_HEAD:
            continue
        if ref.startswith("refs/untaped/"):
            plugins.setdefault(ref.split("/")[2], 0)
        elif ref.startswith(("refs/remotes/", "refs/tags/")):
            plugins.setdefault("workspace", 0)
        elif ref.startswith("refs/heads/"):
            branches.append(ref.removeprefix("refs/heads/"))
        elif ref == "refs/stash":
            stash = True
    foreign = []
    for entry in worktree_entries(store._path):
        if entry.owner is None:
            foreign.append(shown_path(entry.path) if entry.path else str(entry.admin))
        else:
            plugins[entry.owner] = plugins.get(entry.owner, 0) + 1
    if not plugins and not foreign and not branches and not stash:
        return None
    return Released(
        plugins=dict(sorted(plugins.items())),
        foreign_worktrees=tuple(foreign),
        branches=tuple(branches),
        stash=stash,
    )


def _checked_out(store: RepoStore) -> list[str]:
    """The branches registered worktrees have checked out (``worktree list``)."""
    result = store._git(["worktree", "list", "--porcelain"], capture=True, check=False)
    return [
        line.removeprefix("branch ")
        for line in result.text.splitlines()
        if line.startswith("branch ")
    ]


def _removing_path(store: RepoStore) -> Path:
    return store._path.with_name(store._path.name + REMOVING_SUFFIX)


def remove_removing(store: RepoStore) -> None:
    """Delete what an interrupted removal left beside the repo."""
    removing = _removing_path(store)
    if removing.is_dir() and not removing.is_symlink():
        shutil.rmtree(removing, ignore_errors=True)
