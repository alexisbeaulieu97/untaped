"""The repo store: one blobless, full-history bare repository per URL, shared by plugins.

Layout: ``<git.store_dir>/<host>/<path>.git``, keyed by
:func:`~untaped_git.url.store_key`, so the https and ssh URLs of one
repo are one directory. Plugins keep their refs in the same repo, each in its
own namespace or, for one plugin per repo that declares it, the plain-clone
layout (:mod:`untaped_git.namespace`), and the store holds the same thing for
all of them: every commit and tree of the refs any consumer asked for, and the
blobs a consumer read. Consumers say what they read (refs, paths or history);
the store decides how objects are fetched and kept, so nothing here takes a
depth or a filter, and a shallow fetch cannot happen.

:class:`RepoStore` is a plugin's handle on one repo. Its parts run git
through the handle's :class:`~untaped_git.store_git.StoreGit`: fetching
(:mod:`~untaped_git.store_fetch`), blobs and their readers
(:mod:`~untaped_git.store_prefetch`), worktrees
(:mod:`~untaped_git.store_worktrees` and :mod:`~untaped_git.worktree_config`)
and release (:mod:`~untaped_git.store_release`).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Self

from pydantic import BaseModel

from untaped.sdk import ConfigError, ErrorCategory, System, UntapedError, experimental
from untaped.sdk.storage import NO_PROGRESS, ProgressHandle
from untaped_git import (
    plain_clone,
    store_fetch,
    store_prefetch,
    store_release,
    store_worktrees,
    worktree_config,
)
from untaped_git.delta import RefDelta
from untaped_git.hosts import HostAuth
from untaped_git.namespace import ORIGIN_HEAD, check_names, is_glob, layout_for
from untaped_git.records import TreeEntry
from untaped_git.release import Released, Removed
from untaped_git.repo_files import list_repos, private_file, repo_origin, worktree_entries
from untaped_git.run import GitCommandError, GitResult
from untaped_git.store_git import NO_LAZY, StoreGit
from untaped_git.store_prefetch import Prefetched
from untaped_git.store_release import RELEASE_MARK

if TYPE_CHECKING:
    from untaped.sdk.storage import DataRead

#: What ``run()`` refuses: the store's own methods reach the remote.
_NETWORK_VERBS = frozenset(
    {"fetch", "pull", "push", "ls-remote", "clone", "submodule", "fetch-pack", "send-pack"}
)


class _UsesRepo(BaseModel):
    """A private file that holds no data: its presence says the plugin uses the repo."""


class RepoStore:
    """One repo of the store, as one plugin sees it; see the module docstring.

    Build it with :meth:`for_url` or :meth:`owned_by`, which also resolve
    the host's credentials and ``git.untaped_helper_only``; a handle built
    directly has neither. ``plain_clone=True`` declares the plain-clone layout
    (:mod:`untaped_git.namespace`): the plugin's refs are ``origin/*`` and the
    repo's tags, so its worktrees work as in a clone; one plugin per repo
    holds it, and another plugin declaring it is refused (a conflict) while
    that one still has refs or worktrees there. Writers (``fetch``, ``prefetch`` and what calls it,
    ``delete_refs``, ``write_worktree_config``, ``release``) hold the repo's
    lock, and the fetching ones create the repo when it is missing; ``refs``,
    ``ls_tree`` and a handle's ``run`` only read and do not.
    """

    def __init__(
        self,
        path: Path,
        *,
        url: str,
        plugin: str,
        error: type[UntapedError],
        map_error: Callable[[GitCommandError], UntapedError] | None = None,
        plain_clone: bool = False,
    ) -> None:
        self._repo = StoreGit(
            path,
            url=url,
            plugin=plugin,
            layout=layout_for(plugin, plain=plain_clone),
            error=error,
            map_error=map_error,
        )

    @classmethod
    def _open(
        cls,
        path: Path,
        *,
        url: str,
        plugin: str,
        error: type[UntapedError],
        map_error: Callable[[GitCommandError], UntapedError] | None = None,
        plain_clone: bool = False,
        auth: Callable[[str], HostAuth | None] | None = None,
        warn: Callable[[str], None] | None = None,
        helper_only: bool = False,
        sleep: Callable[[float], None] = time.sleep,
    ) -> Self:
        """A handle with its knobs: what :meth:`for_url` resolves, or a test's own."""
        store = cls(
            path,
            url=url,
            plugin=plugin,
            error=error,
            map_error=map_error,
            plain_clone=plain_clone,
        )
        store._repo = replace(
            store._repo,
            auth=auth,
            warn=warn or store._repo.warn,
            helper_only=helper_only,
            sleep=sleep,
        )
        return store

    @classmethod
    def for_url(
        cls,
        url: str,
        *,
        plugin: object,
        error: type[UntapedError],
        map_error: Callable[[GitCommandError], UntapedError] | None = None,
        plain_clone: bool = False,
    ) -> Self:
        """The store repo of ``url`` for ``plugin`` under ``git.store_dir``.

        ``error`` is the plugin's error class: every failure is raised as one,
        keeping git's category (``map_error`` maps a ``GitCommandError``
        itself instead). ``plain_clone`` declares the plain-clone layout (see
        the class docstring).
        """
        from untaped_git.hosts import resolve_host  # noqa: PLC0415  # contracts load lazily
        from untaped_git.settings import git_settings, store_root  # noqa: PLC0415
        from untaped_git.url import store_key  # noqa: PLC0415

        settings = git_settings()
        return cls._open(
            store_root().joinpath(*store_key(url)),
            url=url,
            plugin=_plugin_name(plugin),
            error=error,
            map_error=map_error,
            plain_clone=plain_clone,
            auth=resolve_host,
            helper_only=settings.untaped_helper_only,
        )

    @classmethod
    def owned_by(
        cls,
        plugin: object,
        *,
        error: type[UntapedError],
        map_error: Callable[[GitCommandError], UntapedError] | None = None,
        plain_clone: bool = False,
    ) -> list[Self]:
        """Every store repo holding ``plugin``'s private file, as ``plugin`` sees it.

        Read from disk alone, sorted by path; each handle's URL is the repo's
        label (``remote.origin.url``). A repo without a label is skipped.
        """
        from untaped_git.hosts import resolve_host  # noqa: PLC0415  # contracts load lazily
        from untaped_git.settings import git_settings, store_root  # noqa: PLC0415

        name = _plugin_name(plugin)
        settings = git_settings()
        stores = []
        for repo in list_repos(store_root()):
            label = repo_origin(repo)
            if label is None or not private_file(repo, name).is_file():
                continue
            stores.append(
                cls._open(
                    repo,
                    url=label,
                    plugin=name,
                    error=error,
                    map_error=map_error,
                    plain_clone=plain_clone,
                    auth=resolve_host,
                    helper_only=settings.untaped_helper_only,
                )
            )
        return stores

    @property
    def path(self) -> Path:
        """The bare repository directory."""
        return self._repo.path

    @property
    def url(self) -> str:
        """The URL this store handle fetches from."""
        return self._repo.url

    @property
    def private_file(self) -> Path:
        """This plugin's own file in the repo, ``untaped-<plugin>.json``.

        The plugin writes it (its presence is how the store's report and
        :meth:`owned_by` know the plugin uses the repo); :meth:`release`
        deletes it.
        """
        return private_file(self._repo.path, self._repo.plugin)

    @experimental
    def read_private[M: BaseModel](self, model: type[M]) -> DataRead[M]:
        """This plugin's private file in the repo, read as ``model``.

        Found, absent, unreadable or a newer untaped's (:data:`~untaped.sdk.storage.DataRead`);
        ``Found.refreshed_at`` is the freshness :meth:`write_private` was given.
        """
        # the data file loads on first use
        from untaped.sdk.storage import DataFile  # noqa: PLC0415

        return DataFile(private_file(self._repo.path, self._repo.plugin), model).read()

    @experimental
    def write_private(
        self, value: BaseModel | None = None, *, refreshed_at: datetime | None = None
    ) -> None:
        """Write this plugin's private file in the repo, under the repo's lock.

        The file's presence is how the store's report, :meth:`owned_by` and
        :meth:`release` know the plugin uses the repo; ``value`` is the
        plugin's own data, and ``refreshed_at`` when it was last refreshed,
        for data that can go stale. With no ``value`` the file only marks the
        repo as used: one already there is kept as it is. A newer untaped's file is
        replaced, as the plugin refetched its data. The repo must exist.
        """
        # the data file loads on first use
        from untaped.sdk.storage import DataFile, OtherVersion  # noqa: PLC0415

        path = private_file(self._repo.path, self._repo.plugin)
        if value is None:
            if refreshed_at is not None:
                raise TypeError("write_private(refreshed_at=...) needs the value it dates")
            if path.is_file():  # a presence mark: nothing to lock or write
                return
        with self._repo.locked():
            # Checked under the lock: a release renames the repo aside while holding it.
            if not self.exists():
                raise self._repo.error(
                    f"no repo store repository at {self._repo.path} to keep {path.name} in",
                    category=ErrorCategory.FAILED,
                    system=System.LOCAL,
                )
            try:
                if value is None:
                    DataFile(path, _UsesRepo).write(_UsesRepo(), exclusive=True)
                else:
                    file = DataFile(path, type(value))
                    if isinstance(file.read(), OtherVersion):
                        # the plugin's data, refetched: a newer untaped's copy is replaced
                        path.unlink(missing_ok=True)
                    file.write(value, refreshed_at=refreshed_at)
            except ConfigError as exc:  # the store reports its own failures
                raise self._repo.error(
                    str(exc),
                    hint=exc.hint,
                    category=ErrorCategory.FAILED,
                    system=System.LOCAL,
                ) from exc

    def exists(self) -> bool:
        return self._repo.exists()

    def has_part(self) -> bool:
        """Whether this plugin has a part in the repo, so :meth:`release` has work to do.

        Its private file or refs are there, or a release of its own was
        interrupted (the mark the next fetch, prefetch or :meth:`release` finishes).
        """
        if not self.exists():
            return False
        if self.private_file.is_file() or self._repo.config_get(RELEASE_MARK) == self._repo.plugin:
            return True
        return bool(
            self._repo.git(
                ["for-each-ref", "--count=1", "--format=%(refname)", *self.roots], capture=True
            ).text.strip()
        )

    @property
    def roots(self) -> tuple[str, str]:
        """Where this plugin's branches and tags live in the repo (``for-each-ref`` patterns)."""
        return self._repo.layout.roots

    def ref(self, name: str) -> str:
        """The full ref of ``heads/<branch>`` or ``tags/<tag>`` in this plugin's namespace."""
        try:
            return self._repo.layout.absolute(name)
        except ValueError as exc:
            raise self._repo.error(str(exc), category=ErrorCategory.INVALID) from None

    def worktree_owners(self) -> dict[Path, str | None]:
        """Each registered worktree's directory → the plugin owning it (``None``: a user's own).

        Read from the repo's files; a worktree whose owner stamp names another
        directory (git copied it when the user added one from an owned
        worktree) is a user's own.
        """
        return {
            entry.path: entry.owner
            for entry in worktree_entries(self._repo.path)
            if entry.path is not None
        }

    def relative(self, ref: str) -> str | None:
        """``heads/<b>`` or ``tags/<t>`` for a ref of this plugin's namespace, else ``None``."""
        return self._repo.layout.relative(ref)

    # ── release ────────────────────────────────────────────────────────────

    def release(
        self, *, branches: Sequence[str] = (), unless_in_use: bool = False
    ) -> Released | Removed:
        """Say this plugin no longer needs the repo; it goes when nobody else holds it.

        Deletes the plugin's refs (with the plain-clone layout's record:
        ``refs/remotes/origin/*``, its ``HEAD`` and ``refs/tags/*``, as with a
        deleted clone, and then the record), its private
        file, its worktrees and the local ``branches`` named (``refs/heads/*``
        the caller's own rules let go; one a worktree has checked out is
        refused before anything changes). Then, when another plugin's refs,
        file or worktree, a worktree added by hand, a branch or a stash is
        left, the repo stays (:class:`Released` names who kept it); otherwise
        it is renamed to ``<repo>.git.removing`` and deleted
        (:class:`Removed`). The ``untaped.release`` mark written first lets
        the next fetch or prefetch finish a release that was interrupted, and
        another plugin's interrupted release is finished first.

        ``unless_in_use`` changes nothing while one of this plugin's worktrees
        is registered, checked under the repo lock (:class:`Released` then
        counts it), so a worktree another process just added is never deleted.
        """
        return store_release.release(self._repo, branches=branches, unless_in_use=unless_in_use)

    # ── refs ───────────────────────────────────────────────────────────────

    def refs(self) -> dict[str, str]:
        """This plugin's refs, ``heads/<branch>``/``tags/<tag>`` → oid."""
        if not self.exists():
            return {}
        return self._repo.namespace()

    def delete_refs(self, names: Iterable[str]) -> None:
        """Delete refs of this plugin's namespace (``heads/<b>``, ``tags/<t>``)."""
        refs = []
        for name in names:
            try:
                ref = self._repo.layout.absolute(name)
            except ValueError as exc:
                raise self._repo.error(str(exc), category=ErrorCategory.INVALID) from None
            short = name.partition("/")[2]
            reason = check_names([short])
            if reason is None and is_glob(short):
                reason = f"{name!r} is a glob; delete_refs takes exact refs"
            if reason is None and ref == ORIGIN_HEAD:
                reason = f"{name!r} is the store's own symref, not a branch"
            if reason is not None:
                raise self._repo.error(reason, category=ErrorCategory.INVALID)
            refs.append(ref)
        if not refs:
            return
        with self._repo.locked():
            if self._repo.layout.plain and self.exists():
                plain_clone.claim(self._repo, record=False)
            self._repo.delete(refs)

    # ── fetch ──────────────────────────────────────────────────────────────

    def fetch(
        self,
        *,
        branches: Sequence[str] = (),
        tags: Sequence[str] = (),
        prune: bool = False,
        progress: ProgressHandle = NO_PROGRESS,
    ) -> RefDelta:
        """Fetch ``branches`` and ``tags`` (names or one-``*`` globs) into this plugin's refs.

        Explicit names are listed against the remote first, so refs already
        current are skipped and the rest go in batches of
        :data:`~untaped_git.store_fetch.DEFAULT_FETCH_BATCH_SIZE`; a call with
        globs only goes straight to git. ``prune=True`` deletes every ref of the
        namespace the call's names don't cover, and every covered ref the remote
        no longer has (never the plain-clone layout's tags, never
        ``refs/remotes/origin/HEAD``). A call with no names does nothing,
        ``prune`` included: dropping a whole namespace is ``delete_refs``' job.
        Returns what changed in the namespace. ``progress`` hears
        ``listing refs``, then ``fetching refs`` with the refs fetched so far.
        """
        return store_fetch.fetch(
            self._repo, branches=branches, tags=tags, prune=prune, progress=progress
        )

    # ── prefetch and blob readers ──────────────────────────────────────────

    def prefetch(
        self,
        *,
        trees: Sequence[str] = (),
        paths: Sequence[str] = (),
        history: Sequence[str] = (),
        blobs: Sequence[str] = (),
        progress: ProgressHandle = NO_PROGRESS,
    ) -> None:
        """Fetch, in one round trip per batch, every blob the reads will need.

        ``trees`` are peeled (``<x>^{tree}``: a commit lists its own tree's
        blobs only); ``history`` walks every commit the revisions reach (a
        ``*`` names a ref glob, ``a..b`` a range). ``paths`` limits both.
        ``blobs`` are blob ids fetched as they are, whatever tree holds them
        (a reader that listed the files it needs with :meth:`ls_tree`); they
        travel on stdin, so any number fits. When nothing is missing no
        network call is made. ``progress`` hears
        ``listing missing blobs`` (a walk of the trees or the history, which
        can take seconds), then, when some are missing, ``fetching blobs``
        with the blobs fetched so far. A blob still missing afterwards, or a
        host refusing the fetch, is ``unavailable`` (exit 5).
        """
        store_prefetch.prefetch(
            self._repo, trees=trees, paths=paths, history=history, blobs=blobs, progress=progress
        )

    def prefetched(
        self,
        *,
        trees: Sequence[str] = (),
        paths: Sequence[str] = (),
        blobs: Sequence[str] = (),
        progress: ProgressHandle = NO_PROGRESS,
    ) -> Prefetched:
        """Prefetch ``trees`` (peeled) limited to ``paths``, and ``blobs``; then a handle.

        Calling it with neither ``trees`` nor ``blobs`` is a programming
        error (``ValueError``): the handle would read nothing.
        """
        if not trees and not blobs:
            raise ValueError("prefetched() reads nothing: pass trees, blobs or both")
        self.prefetch(trees=trees, paths=paths, blobs=blobs, progress=progress)
        return Prefetched(self)

    def ls_tree(self, tree: str, paths: Sequence[str] = ()) -> list[TreeEntry]:
        """The entries of ``tree`` (recursive), limited to ``paths``; trees are always present."""
        return store_prefetch.ls_tree(self._repo, tree, paths)

    def run(
        self,
        argv: Sequence[str],
        *,
        capture: bool = False,
        check: bool = True,
        stdin: bytes | str | None = None,
        timeout: float | None = None,
        locale_c: bool = True,
    ) -> GitResult:
        """Run a local ``git <argv>`` on the store repo (never a fetch: use :meth:`fetch`).

        Blob readers (``grep``, ``cat-file``, ``show``, ``archive``) belong on
        a :meth:`prefetched` handle; ``untaped lint`` flags them here.
        """
        if (
            not argv
            or argv[0].startswith("-")
            or argv[0] in _NETWORK_VERBS
            or (argv[0] == "remote" and argv[1:2] != ["get-url"])
            or any(arg == "--remote" or arg.startswith("--remote=") for arg in argv)
        ):
            raise ValueError(
                "run() takes a local git subcommand first; fetch through "
                "RepoStore.fetch() or prefetch()"
            )
        return self._repo.git(
            argv,
            capture=capture,
            check=check,
            stdin=stdin,
            timeout=timeout,
            env=NO_LAZY,
            locale_c=locale_c,
        )

    # ── worktrees ──────────────────────────────────────────────────────────

    def worktree_add(
        self, path: Path, ref: str, *, branch: str | None = None, lock_reason: str | None = None
    ) -> None:
        """Add a worktree at ``path`` on ``ref`` (a new ``branch`` from it, else detached).

        The tip's blobs are prefetched first, so a host that refuses them
        fails before anything is written. The first worktree moves
        ``core.bare`` into the repo's ``config.worktree``. With
        ``lock_reason`` the worktree is added locked (see :meth:`lock_worktree`).
        """
        store_worktrees.add_worktree(self._repo, path, ref, branch=branch, lock_reason=lock_reason)

    def lock_worktree(self, worktree: Path, *, reason: str) -> None:
        """Lock one of this repo's worktrees (``git worktree lock --reason``).

        ``git worktree prune``, which the store and its consumers run at every
        checkout, remove and release, then keeps the entry while its directory
        is moved away or unreachable (an unmounted drive), so
        :meth:`repair_worktree` can still find it. The caller says why, in one
        line; a worktree already locked keeps its lock and reason. An entry
        whose folder is already gone can still be locked by its registered
        path. Releasing the plugin's worktrees removes locked ones too.
        """
        with self._repo.locked():
            worktree_config.lock_worktree(self._repo, worktree, reason=reason)

    def repair_worktree(self, worktree: Path) -> None:
        """Reconnect a worktree moved to ``worktree`` (``git worktree repair``).

        The repo's entry is pointed at the new directory, and the owner stamp,
        which records the absolute path, is rewritten when this plugin owns
        it, also when git already repointed the entry (a hand ``git worktree
        repair`` or ``move``), and so is untaped's credential helper line,
        whose nonce is the folder's. Safe to repeat. Refused when ``worktree`` is
        not one of this repo's worktrees.
        """
        with self._repo.locked():
            worktree_config.repair_worktree(self._repo, worktree)

    def worktrees_under(self, path: Path) -> list[Path]:
        """This plugin's worktrees registered at or under ``path``, sorted.

        Read from each entry's recorded directory, so a folder that was moved
        away or sits on an unmounted drive still lists the worktrees it held.
        """
        return store_worktrees.worktrees_under(self._repo, path)

    def checkout(self, worktree: Path, ref: str) -> None:
        """Check ``ref`` out in one of this repo's worktrees, its blobs prefetched first."""
        store_worktrees.checkout(self._repo, worktree, ref)

    def write_worktree_config(self, worktree: Path) -> None:
        """Write the worktree's own config: owner, refspec, filter, URL and credential helper.

        A consumer calls it after adding a worktree and after each fetch: a
        user's ``git fetch``/``push`` there uses this handle's URL
        (``insteadOf`` over the store's label) and, for https, asks
        ``untaped git credential`` after the user's own helpers (instead of
        them with ``git.untaped_helper_only``). The helper line names no
        profile: the folder's project picks it (its ``profile:``), and
        ``UNTAPED_PROFILE``, as for any untaped command run there.
        """
        worktree_config.write_worktree_config(self._repo, worktree)


def _plugin_name(plugin: object) -> str:
    name = getattr(plugin, "name", plugin)
    if not isinstance(name, str):
        raise TypeError("plugin= takes the caller's plugin: its name, or what names it")
    return name
