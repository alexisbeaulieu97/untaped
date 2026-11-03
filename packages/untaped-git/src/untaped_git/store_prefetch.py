"""How the store fetches the blobs a reader needs: :meth:`RepoStore.prefetch` and its handle.

A blob nobody prefetched costs one round trip in a blobless repo, so blob
readers go through :meth:`RepoStore.prefetched`, whose :class:`Prefetched`
handle exists only after its prefetch; ``GIT_NO_LAZY_FETCH=1`` is set on every
git the store runs except its fetches and its maintenance, so on a git that
honours it a forgotten prefetch fails loudly (an older git fetches lazily, and
the prefetch's guard is the only check).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING

from untaped.sdk import ErrorCategory
from untaped.sdk.storage import NO_PROGRESS, ProgressHandle
from untaped_git.records import TreeEntry
from untaped_git.run import GitCommandError, GitResult
from untaped_git.store_git import NO_LAZY, SLOW_TIMEOUT
from untaped_git.url import url_host
from untaped_git.version import below_floor, git_version

if TYPE_CHECKING:
    from untaped_git.store import RepoStore
    from untaped_git.store_git import StoreGit

#: Object ids per prefetch command (``fetch --stdin``).
PREFETCH_BATCH_SIZE = 5000
_REFUSED = ("unadvertised object", "not our ref", "allow-tip-sha1-in-want")
_REFUSED_HINT = (
    "this host refuses blob fetches by object id (a protocol v0/v1 server without "
    "uploadpack.allowAnySHA1InWant), so the repo store cannot read files there: "
    "upgrade the server, or repair the repo with `git fetch --refetch` without the "
    "filter (git 2.36+), or delete it while no worktree uses it"
)
_OBJECT_ID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
#: An object id's length in each object format.
_ID_LENGTH = {"sha1": 40, "sha256": 64}
#: What a :class:`Prefetched` handle refuses: the store's own writers.
_HANDLE_REFUSED = ("fetch", "checkout", "worktree")


def prefetch(
    store: StoreGit,
    *,
    trees: Sequence[str] = (),
    paths: Sequence[str] = (),
    history: Sequence[str] = (),
    blobs: Sequence[str] = (),
    progress: ProgressHandle = NO_PROGRESS,
) -> None:
    """Fetch every blob the reads will need; :meth:`RepoStore.prefetch` documents it."""
    revisions = [f"{tree}^{{tree}}" for tree in trees]
    for item in history:
        if item.startswith("-"):
            raise store.error(f"{item!r} is not a revision", category=ErrorCategory.INVALID)
        revisions.append(f"--glob={item}" if "*" in item else item)
    for oid in blobs:
        if not _OBJECT_ID.fullmatch(oid):
            raise store.error(f"{oid!r} is not a blob id", category=ErrorCategory.INVALID)
    wanted = list(dict.fromkeys(blobs))
    if not revisions and not wanted:
        return
    with store.locked():
        store.ensure()
        _check_ids(store, wanted)
        progress.update(phase="listing missing blobs")
        missing = _missing_in(store, revisions, paths, wanted)
        if not missing:
            return
        progress.update(phase="fetching blobs", done=0, total=len(missing))
        for start in range(0, len(missing), PREFETCH_BATCH_SIZE):
            batch = missing[start : start + PREFETCH_BATCH_SIZE]
            _prefetch_batch(store, batch)
            progress.update(phase="fetching blobs", done=start + len(batch), total=len(missing))
        left = _missing_in(store, revisions, paths, wanted)
        if left:
            host = url_host(store.url) or store.url
            raise store.fail(
                GitCommandError(
                    f"{len(left)} blobs still missing after a prefetch from {host}",
                    category=ErrorCategory.UNAVAILABLE,
                    hint=_REFUSED_HINT,
                )
            )
        store.maintain()


def _check_ids(store: StoreGit, blobs: Sequence[str]) -> None:
    """Refuse an id of the other object format than this repo's (``invalid``)."""
    if not blobs:
        return
    length = _ID_LENGTH.get(store.config_get("extensions.objectformat") or "sha1", 40)
    for oid in blobs:
        if len(oid) != length:
            raise store.error(f"{oid!r} is not a blob id", category=ErrorCategory.INVALID)


def missing_blobs(store: StoreGit, blobs: Sequence[str]) -> list[str]:
    """The ids among ``blobs`` the repo does not hold, read from stdin without fetching.

    ``rev-list --missing=print`` never fetches, and ``--ignore-missing``
    skips a missing tip: what it prints is present. ``cat-file`` and
    ``mktree`` would fetch each missing blob lazily on a git that does
    not honour ``GIT_NO_LAZY_FETCH``, and ``cat-file`` dies at the first
    on one that does. Before git 2.38 rev-list also hashes each present
    blob it is given (local reads only).
    """
    if not blobs:
        return []
    result = store.git(
        [
            "rev-list",
            "--objects",
            "--no-object-names",
            "--missing=print",
            "--ignore-missing",
            "--stdin",
        ],
        stdin="".join(f"{oid}\n" for oid in blobs),
        capture=True,
        timeout=SLOW_TIMEOUT,
        env=NO_LAZY,
    )
    present = {line for line in result.text.splitlines() if not line.startswith("?")}
    return [oid for oid in blobs if oid not in present]


def _missing_in(
    store: StoreGit, revisions: Sequence[str], paths: Sequence[str], blobs: Sequence[str]
) -> list[str]:
    """The blobs missing from ``revisions`` (limited to ``paths``), then from ``blobs``."""
    missing = store.missing(revisions, paths) if revisions else []
    return list(dict.fromkeys([*missing, *missing_blobs(store, blobs)]))


def _prefetch_batch(store: StoreGit, oids: Sequence[str]) -> None:
    argv = [
        "fetch",
        "--no-tags",
        "--no-write-fetch-head",
        "--recurse-submodules=no",
        "--no-prune",
        "--filter=blob:none",
        "--stdin",
        "origin",
    ]
    try:
        store.network(
            argv,
            stdin="".join(f"{oid}\n" for oid in oids),
            timeout=SLOW_TIMEOUT,
            retry=True,
            config={"fetch.negotiationAlgorithm": "noop"},
            raw=True,
        )
    except GitCommandError as exc:
        host = url_host(store.url) or store.url
        lowered = exc.stderr.lower()
        if any(marker in lowered for marker in _REFUSED):
            raise store.fail(
                GitCommandError(
                    f"{host} refused to send blobs by object id: {exc}",
                    returncode=exc.returncode,
                    stderr=exc.stderr,
                    category=ErrorCategory.UNAVAILABLE,
                    hint=_REFUSED_HINT,
                )
            ) from exc
        if exc.category in (ErrorCategory.FAILED, None) and not below_floor(git_version()):
            exc.category = ErrorCategory.UNAVAILABLE
            exc.args = (f"prefetch from {host} failed: {exc}",)
        raise store.fail(exc) from exc


def ls_tree(store: StoreGit, tree: str, paths: Sequence[str] = ()) -> list[TreeEntry]:
    """The entries of ``tree`` (recursive), limited to ``paths``; trees are always present."""
    result = store.git(
        ["ls-tree", "-r", "-z", "--full-tree", tree, "--", *paths], capture=True, env=NO_LAZY
    )
    entries = []
    for record in result.stdout.split(b"\0"):
        if not record:
            continue
        meta, _, name = record.decode("utf-8", errors="surrogateescape").partition("\t")
        mode, kind, oid = meta.split(" ", 2)
        entries.append(TreeEntry(mode=mode, type=kind, oid=oid, path=name))
    return entries


class Prefetched:
    """A store repo whose blobs for ``trees`` (and ``paths``) and ``blobs`` are present.

    :meth:`run` is the only call through which a consumer runs ``grep``,
    ``cat-file``, ``show`` or ``archive`` on a store repo; it exists only
    after its prefetch. It runs unlocked with ``GIT_NO_LAZY_FETCH``, so a read
    outside the prefetched trees and paths fails instead of fetching.
    """

    def __init__(self, store: RepoStore) -> None:
        self._store = store

    def run(
        self,
        argv: Sequence[str],
        *,
        capture: bool = True,
        check: bool = True,
        stdin: bytes | str | None = None,
        timeout: float | None = None,
        locale_c: bool = True,
    ) -> GitResult:
        """Run a local, read-only ``git <argv>`` on the store repo.

        ``locale_c=False`` keeps the user's locale (it decides how a regex
        treats non-ASCII text).
        """
        if argv and (argv[0] in _HANDLE_REFUSED):
            raise ValueError(f"git {argv[0]} goes through RepoStore, never a Prefetched handle")
        return self._store.run(
            argv, capture=capture, check=check, stdin=stdin, timeout=timeout, locale_c=locale_c
        )
