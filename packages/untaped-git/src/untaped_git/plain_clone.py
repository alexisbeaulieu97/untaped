"""Which plugin holds a store repo's plain-clone refs: the record and the claim.

A plugin declares the plain-clone layout when it opens the store
(``RepoStore.for_url(..., plain_clone=True)``); git keeps no list of who may.
Those refs (``refs/remotes/origin/*``, ``refs/tags/*`` and the
``refs/remotes/origin/HEAD`` symref) can be one plugin's per repo, so the repo
records whose: ``untaped.plainClone`` in its config, written by the
declarer's first writer under the repo lock. Release reads the record to know
whose those refs are, and clears it after deleting them. A declarer is refused
while the record names another plugin that still has plain-clone refs or a
worktree in the repo; a recorded plugin with nothing left there is replaced.
This module is the store's own: it uses :class:`RepoStore`'s internals.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from untaped.sdk import ErrorCategory
from untaped_git.namespace import ORIGIN_HEAD, PLAIN_HEADS, PLAIN_TAGS
from untaped_git.repo_files import worktree_entries

if TYPE_CHECKING:
    from untaped_git.store import RepoStore

#: The repo config key naming the plugin that holds the plain-clone refs.
HOLDER = "untaped.plainClone"


def holder(store: RepoStore) -> str | None:
    """The plugin the repo records as holding its plain-clone refs, or ``None``."""
    return store._config_get(HOLDER)


def claim(store: RepoStore, *, record: bool = True) -> None:
    """Refuse when another plugin holds the plain-clone refs; else record ``store``'s plugin.

    Runs under the caller's repo lock, on an existing repo. With ``record=False``
    it only refuses (a writer that creates nothing, such as a ref deletion).
    """
    current = holder(store)
    if current == store._plugin:
        return
    if current is not None and _holds(store, current):
        raise store._error(
            f"the plain-clone refs of {store._path} are {current}'s: one plugin per repo "
            f"declares the plain-clone layout, and {store._plugin} cannot share them",
            category=ErrorCategory.CONFLICT,
        )
    if record:
        store._git(["config", HOLDER, store._plugin])


def free(store: RepoStore, plugin: str) -> None:
    """Clear the record when it names ``plugin``, after its release deleted its refs."""
    if holder(store) == plugin:
        store._git(["config", "--unset-all", HOLDER], check=False)


def _holds(store: RepoStore, plugin: str) -> bool:
    """Whether ``plugin`` still has a plain-clone ref or a worktree in the repo."""
    refs = store._git(
        ["for-each-ref", "--format=%(refname)", PLAIN_HEADS, PLAIN_TAGS], capture=True
    ).text.split()
    if any(ref != ORIGIN_HEAD for ref in refs):
        return True
    return any(entry.owner == plugin for entry in worktree_entries(store._path))
