"""The per-worktree config of store repos: ``config.worktree`` and each worktree's keys.

A store repo is bare and shared, so whatever a user's own ``git fetch`` or
``push`` in a worktree needs (owner, refspec, filter, URL rewrite and
credential helper) lives in that worktree's config, never the repo's. This module
is the store's own: it uses :class:`RepoStore`'s internals.
"""

from __future__ import annotations

import os
import re
import shlex
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.sdk import ErrorCategory, UntapedError, find_program
from untaped_git.helper_nonce import helper_nonce
from untaped_git.namespace import layout_for
from untaped_git.repo_files import (
    WorktreeEntry,
    config_value,
    names_admin,
    repo_origin,
    worktree_entries,
)
from untaped_git.url import https_origin

if TYPE_CHECKING:
    from untaped_git.store import RepoStore

_PLAIN_REFSPEC = "+refs/heads/*:refs/remotes/origin/*"
_NONCE = re.compile(r"--nonce [0-9a-f]+")
#: The owning plugin's name, in the entry's admin directory (``worktrees/<id>``).
OWNER_MARK = "untaped-owner"


def write_worktree_config(store: RepoStore, worktree: Path) -> None:
    """Write a worktree's own config; :meth:`RepoStore.write_worktree_config` documents it."""
    with store._locked():
        check_worktree(store, worktree)
        enable_worktree_config(store)
        write_owner_config(store, worktree)
        label = repo_origin(store._path)
        # Every rewrite onto the label goes, so an earlier URL spelling
        # (https before ssh, say) never wins over this one.
        for key in _rewrites_onto(store, worktree, label) if label else ():
            _worktree_config(store, worktree, "--unset-all", key, check=False)
        if label and label != store._url:
            _worktree_config(store, worktree, f"url.{store._url}.insteadOf", label)
        origin = https_origin(store._url)
        if origin is None:
            return
        helper = f"credential.{origin}.helper"
        _worktree_config(store, worktree, f"credential.{origin}.useHttpPath", "true")
        _worktree_config(store, worktree, "--unset-all", helper, check=False)
        if store._helper_only:
            _worktree_config(store, worktree, "--add", helper, "")
        nonce = helper_nonce(store._path, worktree, create=True)
        _worktree_config(store, worktree, "--add", helper, _helper_command(nonce))


def enable_worktree_config(store: RepoStore) -> None:
    if store._config_get("extensions.worktreeConfig") == "true":
        return
    store._git(["config", "--file", str(store._path / "config.worktree"), "core.bare", "true"])
    store._git(["config", "--local", "--unset-all", "core.bare"], check=False)
    store._git(["config", "core.repositoryformatversion", "1"])
    store._git(["config", "extensions.worktreeConfig", "true"])
    repair_worktrees(store, stripped=False)


def repair_worktrees(store: RepoStore, *, stripped: bool) -> None:
    """Give each registered worktree its own fetch keys after a shared-config change.

    A worktree workspace owns gets workspace's owner keys. With the shared
    refspec just ``stripped``, a worktree nobody owns (a 10.x worktree, or
    one added by hand) gets the refspec and filter it lost, never an
    owner: release would then remove it.
    """
    if store._config_get("extensions.worktreeConfig") != "true":
        return
    for entry in worktree_entries(store._path):
        if entry.path is None or not names_admin(entry.path / ".git", entry.admin):
            continue
        if entry.owner == "workspace":
            write_owner_config(store, entry.path, owner="workspace")
        elif entry.owner is None and stripped:
            _write_fetch_config(store, entry.path, plain=True)


def write_owner_config(store: RepoStore, worktree: Path, *, owner: str | None = None) -> None:
    owner = owner or store._plugin
    _worktree_config(store, worktree, "untaped.owner", owner)
    # Stamps which worktree is owned: git copies this file into worktrees
    # added from this one, which must stay a user's own.
    _worktree_config(store, worktree, "untaped.worktree", str(worktree.resolve()))
    # And in the entry's admin directory, which git keeps across a move or
    # repair, never copies, and deletes with the entry.
    entry = _entry_of(store, worktree)
    if entry is not None:
        (entry.admin / OWNER_MARK).write_text(f"{owner}\n", encoding="utf-8")
    plain = layout_for(owner).heads == "refs/remotes/origin/"
    _write_fetch_config(store, worktree, plain=plain)


def _write_fetch_config(store: RepoStore, worktree: Path, *, plain: bool) -> None:
    refspec = "remote.origin.fetch"
    _worktree_config(store, worktree, "--unset-all", refspec, check=False)
    if plain:
        _worktree_config(store, worktree, refspec, _PLAIN_REFSPEC)
    # A user's own fetch here brings the blobs of new commits.
    _worktree_config(store, worktree, "remote.origin.partialclonefilter", "")


def check_reason(store: RepoStore, reason: str) -> None:
    """Refuse a lock reason ``git worktree list --porcelain`` could not show on one line."""
    if not reason.strip() or any(char in reason for char in "\n\r\0"):
        raise store._error(
            f"a worktree lock reason is one non-empty line, not {reason!r}",
            category=ErrorCategory.INVALID,
        )


def lock_worktree(store: RepoStore, worktree: Path, *, reason: str) -> None:
    """Lock one of the repo's worktrees, under the caller's lock; one already locked stays as is.

    The folder may be gone: an entry registered at ``worktree`` is locked all
    the same. :meth:`RepoStore.lock_worktree` documents it.
    """
    check_reason(store, reason)
    real = Path(os.path.realpath(worktree))
    entry = _entry_of(store, worktree) or next(
        (e for e in worktree_entries(store._path) if e.path == real and not worktree.exists()),
        None,
    )
    if entry is None:
        raise _not_a_worktree(store, worktree)
    if (entry.admin / "locked").exists():
        return
    store._git(["worktree", "lock", "--reason", reason, str(entry.path or real)])


def repair_worktree(store: RepoStore, worktree: Path) -> None:
    """Repair a moved worktree, under the caller's lock; see :meth:`RepoStore.repair_worktree`."""
    entry = _entry_of(store, worktree)
    if entry is None:
        raise _not_a_worktree(store, worktree)
    store._git(["worktree", "repair", os.path.abspath(worktree)])
    if _owned_before_repair(store, entry):
        _worktree_config(store, worktree, "untaped.worktree", str(worktree.resolve()))
        _renew_helper_nonce(store, worktree)


def _renew_helper_nonce(store: RepoStore, worktree: Path) -> None:
    """Give untaped's helper line the moved worktree's nonce, keeping the rest of the line."""
    result = store._git(
        ["config", "--worktree", "--null", "--get-regexp", r"^credential\..*\.helper$"],
        cwd=worktree,
        capture=True,
        check=False,
    )
    nonce = helper_nonce(store._path, worktree, create=True)
    for entry in result.text.split("\0"):
        key, _, value = entry.partition("\n")
        renewed = _NONCE.sub(f"--nonce {nonce}", value)
        if key and renewed != value:
            _worktree_config(store, worktree, "--fixed-value", "--replace-all", key, renewed, value)


def _entry_of(store: RepoStore, worktree: Path) -> WorktreeEntry | None:
    """The entry whose admin directory ``worktree``'s ``.git`` file names, if any."""
    return next(
        (e for e in worktree_entries(store._path) if names_admin(worktree / ".git", e.admin)),
        None,
    )


def _owned_before_repair(store: RepoStore, entry: WorktreeEntry) -> bool:
    """Whether ``entry`` is this plugin's, read before its stamp is rewritten.

    An owner counts when its stamp names the entry's recorded directory, or,
    once git already repointed the entry (a hand ``git worktree repair`` or
    ``move``), when the entry's own admin directory holds this plugin's mark.
    A hand-added worktree inherits the stamp but never the mark, so it stays
    the user's own.
    """
    if entry.owner is not None:
        return entry.owner == store._plugin
    try:
        mark = (entry.admin / OWNER_MARK).read_text(encoding="utf-8").strip()
    except OSError:
        return False
    owner = config_value(entry.admin / "config.worktree", "untaped", "owner")
    return mark == owner == store._plugin


def check_worktree(store: RepoStore, worktree: Path) -> None:
    """Refuse a path that is not one of this repo's worktrees (a user's own clone, say)."""
    result = store._git(
        ["rev-parse", "--git-common-dir"],
        cwd=worktree,
        capture=True,
        check=False,
    )
    common = result.text.strip()
    # Relative to the worktree when git prints it relative (``--path-format`` is 2.31+).
    if result.returncode != 0 or (worktree / common).resolve() != store._path.resolve():
        raise _not_a_worktree(store, worktree)


def _not_a_worktree(store: RepoStore, worktree: Path) -> UntapedError:
    return store._error(
        f"{worktree} is not a worktree of repo store {store._path}",
        category=ErrorCategory.INVALID,
    )


def _rewrites_onto(store: RepoStore, worktree: Path, label: str) -> list[str]:
    result = store._git(
        ["config", "--worktree", "--null", "--get-regexp", r"^url\..*\.insteadof$"],
        cwd=worktree,
        capture=True,
        check=False,
    )
    keys = []
    for entry in result.text.split("\0"):
        key, _, value = entry.partition("\n")
        if key and value == label:
            keys.append(key)
    return keys


def _worktree_config(store: RepoStore, worktree: Path, *args: str, check: bool = True) -> None:
    store._git(["config", "--worktree", *args], cwd=worktree, check=check)


def _helper_command(nonce: str | None) -> str:
    executable = find_program("untaped") or str(Path(sys.argv[0]).resolve())
    command = f"!{shlex.quote(executable)} git credential"
    return f"{command} --nonce {nonce}" if nonce else command
