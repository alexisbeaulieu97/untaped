"""How a plugin lets go of a store repo: :meth:`RepoStore.release` and its recovery.

``release`` writes the ``untaped.release`` mark, deletes the plugin's refs,
private file and worktrees, then keeps the repo while anyone else holds it or
renames it aside and deletes it. The next fetch or prefetch finishes a release
that was interrupted from the mark. This module is the store's own: it runs
git through :class:`~untaped_git.store_git.StoreGit`.
"""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.sdk import ErrorCategory, System, shown_path
from untaped.sdk.storage import dir_bytes
from untaped_git import plain_clone
from untaped_git.namespace import (
    ORIGIN_HEAD,
    PLAIN_HEADS,
    PLAIN_TAGS,
    check_names,
    is_glob,
    layout_for,
)
from untaped_git.release import Released, Removed
from untaped_git.repo_files import (
    WorktreeEntry,
    private_file,
    private_files,
    worktree_entries,
)

if TYPE_CHECKING:
    from untaped_git.store_git import StoreGit

#: The mark ``release()`` writes before deleting anything; ``StoreGit.ensure()`` finishes from it.
RELEASE_MARK = "untaped.release"
#: Where a repo nobody holds goes, atomically, before it is deleted.
REMOVING_SUFFIX = ".removing"
#: Refs that are some user's data: a repo holding any stays on release.
_HELD_ROOTS = ("refs/untaped/", "refs/remotes/", "refs/heads/", "refs/tags/", "refs/stash")


def release(
    store: StoreGit, *, branches: Sequence[str] = (), unless_in_use: bool = False
) -> Released | Removed:
    """Release ``store``'s plugin from the repo; :meth:`RepoStore.release` documents it."""
    refs = []
    for name in branches:
        reason = check_names([name])
        if reason is None and is_glob(name):
            reason = f"{name!r} is a glob; release takes exact branch names"
        if reason is not None:
            raise store.error(reason, category=ErrorCategory.INVALID)
        refs.append(f"refs/heads/{name}")
    with store.locked():
        if not store.exists():
            remove_removing(store)
            return Removed()
        if unless_in_use and any(
            entry.owner == store.plugin and entry.path is not None and entry.path.is_dir()
            for entry in worktree_entries(store.path)
        ):
            return _holders(store) or Released()
        busy = set(_checked_out(store)) & set(refs)
        if busy:
            names = ", ".join(sorted(ref.removeprefix("refs/heads/") for ref in busy))
            raise store.error(
                f"cannot release {store.path}: a worktree has {names} checked out",
                category=ErrorCategory.CONFLICT,
            )
        marked = store.config_get(RELEASE_MARK)
        if marked is not None and marked != store.plugin:
            # Another plugin's interrupted release: finish it before the mark is reused.
            finish_release(store, marked, ())
        store.git(["config", RELEASE_MARK, store.plugin])
        finish_release(store, store.plugin, refs)
        return settle(store)


def finish_release(store: StoreGit, plugin: str, branches: Sequence[str]) -> None:
    """Delete ``plugin``'s refs, private file and worktrees, and ``branches``.

    Its own namespace's refs always, and the plain-clone layout's when the repo
    records ``plugin`` as their holder; the record goes last, so an interrupted
    release still knows whose they are.
    """
    plain = plain_clone.holder(store) == plugin
    roots = [*layout_for(plugin).roots, *((PLAIN_HEADS, PLAIN_TAGS) if plain else ())]
    refs = store.git(["for-each-ref", "--format=%(refname)", *roots], capture=True).text.split()
    if plain:
        refs.append(ORIGIN_HEAD)
    store.delete(dict.fromkeys([*refs, *branches]))
    private_file(store.path, plugin).unlink(missing_ok=True)
    for entry in worktree_entries(store.path):
        if entry.owner == plugin:
            remove_worktree(store, entry)
    store.git(["worktree", "prune"], check=False)
    plain_clone.free(store, plugin)


def remove_worktree(store: StoreGit, entry: WorktreeEntry) -> None:
    if entry.path is not None:
        removed = store.git(
            ["worktree", "remove", "--force", "--force", str(entry.path)], check=False
        )
        if removed.returncode == 0:
            return
        shutil.rmtree(entry.path, ignore_errors=True)
    shutil.rmtree(entry.admin, ignore_errors=True)


def settle(store: StoreGit) -> Released | Removed:
    """Clear the mark when someone still holds the repo, else remove it."""
    released = _holders(store)
    if released is not None:
        store.git(["config", "--unset-all", RELEASE_MARK], check=False)
        return released
    freed = dir_bytes(store.path)
    removing = _removing_path(store)
    remove_removing(store)
    try:
        store.path.rename(removing)
    except OSError as exc:
        raise store.error(
            f"could not remove repo store directory {store.path}: {exc.strerror or exc}",
            category=ErrorCategory.FAILED,
            system=System.LOCAL,
        ) from exc
    shutil.rmtree(removing, ignore_errors=True)
    return Removed(freed)


def _holders(store: StoreGit) -> Released | None:
    """Who still holds the repo, or ``None`` when nobody does."""
    refs = store.git(
        ["for-each-ref", "--format=%(refname)", *_HELD_ROOTS], capture=True
    ).text.split()
    plugins: dict[str, int] = dict.fromkeys(private_files(store.path), 0)
    plain = plain_clone.holder(store)
    branches, other, stash = [], [], False
    for ref in refs:
        if ref == ORIGIN_HEAD:
            continue
        if ref.startswith("refs/untaped/"):
            plugins.setdefault(ref.split("/")[2], 0)
        elif ref.startswith((PLAIN_HEADS, PLAIN_TAGS)) and plain is not None:
            plugins.setdefault(plain, 0)
        elif ref.startswith(("refs/remotes/", "refs/tags/")):
            other.append(ref)
        elif ref.startswith("refs/heads/"):
            branches.append(ref.removeprefix("refs/heads/"))
        elif ref == "refs/stash":
            stash = True
    foreign = []
    for entry in worktree_entries(store.path):
        if entry.owner is None:
            foreign.append(shown_path(entry.path) if entry.path else str(entry.admin))
        else:
            plugins[entry.owner] = plugins.get(entry.owner, 0) + 1
    if not plugins and not foreign and not branches and not other and not stash:
        return None
    return Released(
        plugins=dict(sorted(plugins.items())),
        foreign_worktrees=tuple(foreign),
        branches=tuple(branches),
        refs=tuple(other),
        stash=stash,
    )


def _checked_out(store: StoreGit) -> list[str]:
    """The branches registered worktrees have checked out (``worktree list``)."""
    result = store.git(["worktree", "list", "--porcelain"], capture=True, check=False)
    return [
        line.removeprefix("branch ")
        for line in result.text.splitlines()
        if line.startswith("branch ")
    ]


def _removing_path(store: StoreGit) -> Path:
    return store.path.with_name(store.path.name + REMOVING_SUFFIX)


def remove_removing(store: StoreGit) -> None:
    """Delete what an interrupted removal left beside the repo."""
    removing = _removing_path(store)
    if removing.is_dir() and not removing.is_symlink():
        shutil.rmtree(removing, ignore_errors=True)
