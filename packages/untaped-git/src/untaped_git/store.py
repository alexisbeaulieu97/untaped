"""The repo store: one blobless, full-history bare repository per URL, shared by plugins.

Layout: ``<git.store_dir>/<host>/<path>.git``, keyed by
:func:`~untaped_git.url.store_key`, so the https and ssh URLs of one
repo are one directory. Workspace, github's sweep and ansible keep their refs
in the same repo, each in its own namespace
(:mod:`untaped_git.namespace`), and the store holds the same thing for
all of them: every commit and tree of the refs any consumer asked for, and the
blobs a consumer read. Consumers say what they read (refs, paths or history);
the store decides how objects are fetched and kept, so nothing here takes a
depth or a filter, and a shallow fetch cannot happen.

Every store fetch passes ``--no-tags`` (tags move only by the explicit
refspecs the store builds), ``--no-write-fetch-head``, ``--filter=blob:none``
and ``--prune`` or ``--no-prune`` explicitly, so a user's global
``fetch.prune`` never decides; it names the repo with ``--git-dir`` and never
runs from a worktree. ``remote.origin.url`` is written once at creation as a
label and never repointed: a fetch of another spelling of the URL rewrites
the label to it with a command-scope ``url.<url>.insteadOf``, and the
credentials (from :class:`~untaped_git.hosts.GitHost`) are scoped to
the URL being fetched.

A blob nobody prefetched costs one round trip in a blobless repo, so blob
readers go through :meth:`RepoStore.prefetched`, whose handle exists only
after its prefetch; ``GIT_NO_LAZY_FETCH=1`` is set on every git the store runs
except its fetches and its maintenance, so on a git that honours it a
forgotten prefetch fails loudly (an older git fetches lazily, and the
prefetch's guard is the only check). Maintenance is the store's second command after a fetch or
prefetch, under the same lock with its own timeout; its failure is a warning,
never a failed fetch, because the data landed before it ran.
"""

from __future__ import annotations

import base64
import os
import re
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Self

from pydantic import BaseModel

from untaped.sdk import (
    NO_PROGRESS,
    Diagnostics,
    ErrorCategory,
    GitCommandError,
    GitResult,
    ProgressHandle,
    UntapedError,
    attribution,
    experimental,
    http_of,
    run_git,
)
from untaped_git import store_release, worktree_config
from untaped_git.delta import RefDelta, diff_refs
from untaped_git.hosts import Credential, HostAuth
from untaped_git.namespace import (
    ORIGIN_HEAD,
    check_names,
    covered,
    is_glob,
    layout_for,
)
from untaped_git.records import TreeEntry
from untaped_git.release import Released, Removed
from untaped_git.repo_files import (
    list_repos,
    private_file,
    repo_origin,
    worktree_entries,
)
from untaped_git.repo_lock import repo_lock
from untaped_git.settings import GitSettings
from untaped_git.store_release import RELEASE_MARK
from untaped_git.url import https_origin, url_host
from untaped_git.version import below_floor, floor_text, git_version, version_text

if TYPE_CHECKING:
    from untaped.sdk import DataRead

_diag = Diagnostics()

#: Explicit refs fetched per command: a dropped connection loses one batch.
DEFAULT_FETCH_BATCH_SIZE = 50
#: Object ids per prefetch command (``fetch --stdin``).
PREFETCH_BATCH_SIZE = 5000
TIMEOUT = 60.0
SLOW_TIMEOUT = 600.0
MAINTENANCE_TIMEOUT = 600.0
LOCK_TIMEOUT = 600.0
ATTEMPTS = 3
#: The layout mark the policy writes on every store repo.
LAYOUT = "3"
#: Every key the store relies on, at repo scope where it beats a user's globals,
#: in the form git reads first. No ``fetch.prune``/``fetch.pruneTags``: every
#: store command line says ``--prune`` or ``--no-prune``, and a repo-scope value
#: would reach the user's own fetches in every worktree.
POLICY: Mapping[str, str] = {
    "untaped.store": LAYOUT,
    "fetch.unpackLimit": "1",
    "gc.auto": "6700",
    "gc.autoDetach": "false",
    "gc.autoPackLimit": "10",
    # Git's default, restated so a global ``now`` cannot prune under an unlocked handle read.
    "gc.pruneExpire": "2.weeks.ago",
}
_NO_LAZY = {"GIT_NO_LAZY_FETCH": "1"}
_NETWORK = {"maintenance.auto": "false"}
#: What ``run()`` refuses: the store's own methods reach the remote.
_NETWORK_VERBS = frozenset(
    {"fetch", "pull", "push", "ls-remote", "clone", "submodule", "fetch-pack", "send-pack"}
)
_FILTER_IGNORED = "filtering not recognized by server"
_REFUSED = ("unadvertised object", "not our ref", "allow-tip-sha1-in-want")
#: How git reports a read of an object it may not fetch lazily: older gits name
#: the promisor remote, newer ones (2.55) only say an object id is not there.
_LAZY_READ = re.compile(r"lazy fetch|promisor remote|not a valid object name [0-9a-f]{40,64}\b")
_REFUSED_HINT = (
    "this host refuses blob fetches by object id (a protocol v0/v1 server without "
    "uploadpack.allowAnySHA1InWant), so the repo store cannot read files there: "
    "upgrade the server, or repair the repo with `git fetch --refetch` without the "
    "filter (git 2.36+), or delete it while no worktree uses it"
)
_LAZY_HINT = (
    "if this is a file's content, read it through RepoStore.prefetched(): it was never prefetched"
)
_STALE_LEFTOVER_SECONDS = 3600.0
_OBJECT_ID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
#: An object id's length in each object format.
_ID_LENGTH = {"sha1": 40, "sha256": 64}
_HANDLE_REFUSED = ("fetch", "checkout", "worktree")


class _UsesRepo(BaseModel):
    """A private file that holds no data: its presence says the plugin uses the repo."""


class RepoStore:
    """One repo of the store, as one plugin sees it; see the module docstring.

    Build it with :meth:`for_url` or :meth:`owned_by`, which also resolve
    the host's credentials and ``git.untaped_helper_only``; a handle built
    directly has neither. Writers (``fetch``, ``prefetch`` and what calls it,
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
    ) -> None:
        self._path = path
        self._url = url
        self._plugin = plugin
        self._layout = layout_for(plugin)
        self._plain = self._layout.heads == "refs/remotes/origin/"
        self._error = error
        self._map_error = map_error
        self._resolved: tuple[HostAuth | None] | None = None
        # Set by ``_open``: the host lookup, warning sink, helper policy and retry pause.
        self._auth: Callable[[str], HostAuth | None] | None = None
        self._warn: Callable[[str], None] = _diag.warning
        self._helper_only = False
        self._sleep: Callable[[float], None] = time.sleep

    @classmethod
    def _open(
        cls,
        path: Path,
        *,
        url: str,
        plugin: str,
        error: type[UntapedError],
        map_error: Callable[[GitCommandError], UntapedError] | None = None,
        auth: Callable[[str], HostAuth | None] | None = None,
        warn: Callable[[str], None] | None = None,
        helper_only: bool = False,
        sleep: Callable[[float], None] = time.sleep,
    ) -> Self:
        """A handle with its knobs: what :meth:`for_url` resolves, or a test's own."""
        store = cls(path, url=url, plugin=plugin, error=error, map_error=map_error)
        store._auth = auth
        store._warn = warn or _diag.warning
        store._helper_only = helper_only
        store._sleep = sleep
        return store

    @classmethod
    def for_url(
        cls,
        url: str,
        *,
        plugin: object,
        error: type[UntapedError],
        map_error: Callable[[GitCommandError], UntapedError] | None = None,
    ) -> Self:
        """The store repo of ``url`` for ``plugin`` under ``git.store_dir``.

        ``error`` is the plugin's error class: every failure is raised as one,
        keeping git's category (``map_error`` maps a ``GitCommandError``
        itself instead).
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
                    auth=resolve_host,
                    helper_only=settings.untaped_helper_only,
                )
            )
        return stores

    @property
    def path(self) -> Path:
        """The bare repository directory."""
        return self._path

    @property
    def url(self) -> str:
        """The URL this store handle fetches from."""
        return self._url

    @property
    def private_file(self) -> Path:
        """This plugin's own file in the repo, ``untaped-<plugin>.json``.

        The plugin writes it (its presence is how the store's report and
        :meth:`owned_by` know the plugin uses the repo); :meth:`release`
        deletes it.
        """
        return private_file(self._path, self._plugin)

    @experimental
    def read_private[M: BaseModel](self, model: type[M]) -> DataRead[M]:
        """This plugin's private file in the repo, read as ``model``.

        Found, absent, unreadable or a newer untaped's (:data:`~untaped.sdk.DataRead`);
        ``Found.refreshed_at`` is the freshness :meth:`write_private` was given.
        A file with no stamp is 11.0's, which wrote the same fields at the top level.
        """
        from untaped.sdk import read_data_file  # noqa: PLC0415  # the data file loads on first use

        return read_data_file(private_file(self._path, self._plugin), model, unstamped="accept")

    @experimental
    def write_private(
        self, value: BaseModel | None = None, *, refreshed_at: datetime | None = None
    ) -> None:
        """Write this plugin's private file in the repo, under the repo's lock.

        The file's presence is how the store's report, :meth:`owned_by` and
        :meth:`release` know the plugin uses the repo; ``value`` is the
        plugin's own data, and ``refreshed_at`` when it was last refreshed,
        for data that can go stale. With no ``value`` the file only marks the
        repo as used: one already there is kept as it is. The repo must exist.
        """
        from untaped.sdk import write_data_file  # noqa: PLC0415  # the data file loads on first use

        path = private_file(self._path, self._plugin)
        if value is None:
            if refreshed_at is not None:
                raise TypeError("write_private(refreshed_at=...) needs the value it dates")
            if path.is_file():  # a presence mark: nothing to lock or write
                return
        with self._locked():
            # Checked under the lock: a release renames the repo aside while holding it.
            if not self.exists():
                raise self._error(
                    f"no repo store repository at {self._path} to keep {path.name} in",
                    category=ErrorCategory.FAILED,
                    system="local",
                )
            try:
                if value is None:
                    write_data_file(path, _UsesRepo(), exclusive=True)
                else:
                    write_data_file(path, value, refreshed_at=refreshed_at)
            except OSError as exc:
                raise self._error(
                    f"could not write {path}: {exc.strerror or exc}",
                    category=ErrorCategory.FAILED,
                    system="local",
                ) from exc

    def exists(self) -> bool:
        return (self._path / "HEAD").is_file()

    def has_part(self) -> bool:
        """Whether this plugin has a part in the repo, so :meth:`release` has work to do.

        Its private file or refs are there, or a release of its own was
        interrupted (the mark the next fetch, prefetch or :meth:`release` finishes).
        """
        if not self.exists():
            return False
        if self.private_file.is_file() or self._config_get(RELEASE_MARK) == self._plugin:
            return True
        return bool(
            self._git(
                ["for-each-ref", "--count=1", "--format=%(refname)", *self.roots], capture=True
            ).text.strip()
        )

    @property
    def roots(self) -> tuple[str, str]:
        """Where this plugin's branches and tags live in the repo (``for-each-ref`` patterns)."""
        return self._layout.roots

    def ref(self, name: str) -> str:
        """The full ref of ``heads/<branch>`` or ``tags/<tag>`` in this plugin's namespace."""
        try:
            return self._layout.absolute(name)
        except ValueError as exc:
            raise self._error(str(exc), category=ErrorCategory.INVALID) from None

    def worktree_owners(self) -> dict[Path, str | None]:
        """Each registered worktree's directory → the plugin owning it (``None``: a user's own).

        Read from the repo's files; a worktree whose owner stamp names another
        directory (git copied it when the user added one from an owned
        worktree) is a user's own.
        """
        return {
            entry.path: entry.owner
            for entry in worktree_entries(self._path)
            if entry.path is not None
        }

    def relative(self, ref: str) -> str | None:
        """``heads/<b>`` or ``tags/<t>`` for a ref of this plugin's namespace, else ``None``."""
        return self._layout.relative(ref)

    # ── lifecycle ──────────────────────────────────────────────────────────

    def _ensure(self) -> bool:
        """Create the repo when missing and reconcile its policy; ``True`` when created.

        ``fetch``, ``prefetch`` and ``checkout`` call it first under the lock,
        so the next of them also finishes an interrupted release.
        """
        store_release.remove_removing(self)
        marked = self._config_get(RELEASE_MARK) if self.exists() else None
        if marked is not None:
            # An interrupted release: finish it before anything else uses the repo.
            store_release.finish_release(self, marked, ())
            store_release.settle(self)
        created = not self.exists()
        if created:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise self._error(
                    f"could not create repo store directory {self._path.parent}: "
                    f"{exc.strerror or exc}",
                    category=ErrorCategory.FAILED,
                    system="local",
                ) from exc
            # An empty template keeps a user's template hooks out of store repos.
            self._git(["init", "--bare", "--quiet", "--template=", str(self._path)], bare=False)
        current = self._config_list()
        # Checked on every call, so a creation interrupted after ``init`` heals.
        if "remote.origin.url" not in current:
            self._git(["config", "remote.origin.url", self._url])
        for key, value in POLICY.items():
            if current.get(key.lower()) != [value]:
                self._git(["config", "--local", "--replace-all", key, value])
        if "remote.origin.fetch" in current:
            # A 10.x workspace repo's shared refspec: each worktree carries its own now.
            self._git(["config", "--local", "--unset-all", "remote.origin.fetch"])
            if worktree_entries(self._path):
                worktree_config.enable_worktree_config(self)
                worktree_config.repair_worktrees(self, stripped=True)
        return created

    # ── release ────────────────────────────────────────────────────────────

    def release(
        self, *, branches: Sequence[str] = (), unless_in_use: bool = False
    ) -> Released | Removed:
        """Say this plugin no longer needs the repo; it goes when nobody else holds it.

        Deletes the plugin's refs (workspace's: ``refs/remotes/origin/*``, its
        ``HEAD`` and ``refs/tags/*``, as with a deleted clone), its private
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
        return store_release.release(self, branches=branches, unless_in_use=unless_in_use)

    # ── refs ───────────────────────────────────────────────────────────────

    def refs(self) -> dict[str, str]:
        """This plugin's refs, ``heads/<branch>``/``tags/<tag>`` → oid."""
        if not self.exists():
            return {}
        return self._namespace()

    def delete_refs(self, names: Iterable[str]) -> None:
        """Delete refs of this plugin's namespace (``heads/<b>``, ``tags/<t>``)."""
        refs = []
        for name in names:
            try:
                ref = self._layout.absolute(name)
            except ValueError as exc:
                raise self._error(str(exc), category=ErrorCategory.INVALID) from None
            short = name.partition("/")[2]
            reason = check_names([short])
            if reason is None and is_glob(short):
                reason = f"{name!r} is a glob; delete_refs takes exact refs"
            if reason is None and ref == ORIGIN_HEAD:
                reason = f"{name!r} is the store's own symref, not a branch"
            if reason is not None:
                raise self._error(reason, category=ErrorCategory.INVALID)
            refs.append(ref)
        if not refs:
            return
        with self._locked():
            self._delete(refs)

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
        :data:`DEFAULT_FETCH_BATCH_SIZE`; a call with globs only goes straight
        to git. ``prune=True`` deletes every ref of the namespace the call's
        names don't cover, and every covered ref the remote no longer has
        (never workspace's tags, never ``refs/remotes/origin/HEAD``). A call
        with no names does nothing, ``prune`` included: dropping a whole
        namespace is ``delete_refs``' job. Returns what changed in the namespace.
        ``progress`` hears ``listing refs``, then ``fetching refs`` with the
        refs fetched so far.
        """
        reason = check_names([*branches, *tags])
        if reason is not None:
            raise self._error(reason, category=ErrorCategory.INVALID)
        if not branches and not tags:
            return RefDelta()
        with self._locked():
            self._ensure()
            self._remove_stale_leftovers()
            before = self._namespace()
            if all(is_glob(name) for name in (*branches, *tags)):
                progress.update(phase="fetching refs")  # one git call: no counts
                stderr = self._fetch_globs(branches, tags, prune=prune)
            else:
                stderr = self._fetch_listed(
                    branches, tags, prune=prune, before=before, progress=progress
                )
            if self._plain:
                self._write_origin_head(branches, tags)
            after = self._namespace()
            delta = diff_refs(before, after)
            self._record_filter(stderr, delta)
            self._maintain()
        return delta

    def _fetch_globs(self, branches: Sequence[str], tags: Sequence[str], *, prune: bool) -> str:
        layout = self._layout
        heads = layout.refspecs("heads", branches)
        tag_specs = layout.refspecs("tags", tags)
        stderr: list[str] = []
        if layout.prune_tags:
            # One command: --prune applies to every refspec on it, all prunable.
            stderr.append(self._fetch_refspecs([*heads, *tag_specs], prune=prune))
        else:
            # --prune never shares a command line with a refspec it must not prune.
            if heads:
                stderr.append(self._fetch_refspecs(heads, prune=prune))
            if tag_specs:
                stderr.append(self._fetch_refspecs(tag_specs, prune=False))
        if prune:
            self._delete(
                layout.absolute(ref)
                for ref in self._namespace()
                if layout.prunable(ref) and not covered(ref, branches, tags)
            )
        return "".join(stderr)

    def _fetch_listed(
        self,
        branches: Sequence[str],
        tags: Sequence[str],
        *,
        prune: bool,
        before: Mapping[str, str],
        progress: ProgressHandle,
    ) -> str:
        layout = self._layout
        progress.update(phase="listing refs")
        remote = self._list_remote(heads=bool(branches), tags=bool(tags))
        wanted = [
            ref
            for ref, oid in remote.items()
            if covered(ref, branches, tags) and before.get(ref) != oid
        ]
        stderr: list[str] = []
        progress.update(phase="fetching refs", done=0, total=len(wanted))
        for start in range(0, len(wanted), DEFAULT_FETCH_BATCH_SIZE):
            batch = wanted[start : start + DEFAULT_FETCH_BATCH_SIZE]
            specs = []
            for ref in batch:
                kind, _, name = ref.partition("/")
                specs.extend(layout.refspecs(kind, [name]))
            stderr.append(self._fetch_refspecs(specs, prune=False))
            progress.update(phase="fetching refs", done=start + len(batch), total=len(wanted))
        if prune:
            self._delete(
                layout.absolute(ref)
                for ref in self._namespace()
                if layout.prunable(ref) and (not covered(ref, branches, tags) or ref not in remote)
            )
        return "".join(stderr)

    def _list_remote(self, *, heads: bool, tags: bool) -> dict[str, str]:
        patterns = [*(["refs/heads/*"] if heads else []), *(["refs/tags/*"] if tags else [])]
        result = self._network(["ls-remote", "--refs", "origin", *patterns], capture=True)
        listed: dict[str, str] = {}
        for line in result.text.splitlines():
            oid, _, ref = line.partition("\t")
            if ref.startswith("refs/heads/"):
                listed[f"heads/{ref.removeprefix('refs/heads/')}"] = oid
            elif ref.startswith("refs/tags/"):
                listed[f"tags/{ref.removeprefix('refs/tags/')}"] = oid
        return listed

    def _fetch_refspecs(self, refspecs: Sequence[str], *, prune: bool) -> str:
        if not refspecs:
            return ""
        argv = [
            "fetch",
            "--no-tags",
            "--no-write-fetch-head",
            "--recurse-submodules=no",
            "--filter=blob:none",
            "--prune" if prune else "--no-prune",
            # Checked per command: one unshallowing fetch removes the file.
            *(["--unshallow"] if (self._path / "shallow").exists() else []),
            "origin",
            *refspecs,
        ]
        return self._network(argv, timeout=SLOW_TIMEOUT, retry=True).stderr

    def _write_origin_head(self, branches: Sequence[str], tags: Sequence[str]) -> None:
        """Point workspace's ``refs/remotes/origin/HEAD`` at the remote's default branch.

        The branch is the recorded ``untaped.defaultBranch`` (``default_branch()``
        records it); with none recorded, or one whose ref a prune removed
        (the remote renamed it), the remote is asked once and the answer recorded.
        A recorded branch this call didn't name is left alone: its ref is simply
        not fetched yet.
        """
        symref = ["symbolic-ref", "--quiet", ORIGIN_HEAD]
        target = self._git(symref, capture=True, check=False).text.strip()
        if target and self._has_ref(target):
            return
        branch = self._config_get("untaped.defaultBranch")
        if branch is None or (
            covered(f"heads/{branch}", branches, tags)
            and not self._has_ref(f"refs/remotes/origin/{branch}")
        ):
            branch = self._remote_default_branch()
        ref = f"refs/remotes/origin/{branch}"
        if branch is not None and self._has_ref(ref):
            self._git(["symbolic-ref", ORIGIN_HEAD, ref])

    def _has_ref(self, ref: str) -> bool:
        verify = ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"]
        return self._git(verify, check=False).returncode == 0

    def _remote_default_branch(self) -> str | None:
        result = self._network(["ls-remote", "--symref", "origin", "HEAD"], capture=True)
        branch = parse_symref(result.text)
        if branch is not None:
            record_default_branch(self._path, branch)
        return branch

    def _record_filter(self, stderr: str, delta: RefDelta) -> None:
        """Record whether the host honoured ``--filter`` (``untaped.filter``).

        Only a fetch that brought something says: the warning, confirmed by
        the new tips' trees holding every blob, means ``ignored``.
        """
        tips = [*delta.added.values(), *(move.new for move in delta.moved.values())]
        if not tips:
            return
        value = "honoured"
        if _FILTER_IGNORED in stderr.lower() and not self._missing(
            [f"{tip}^{{tree}}" for tip in tips]
        ):
            value = "ignored"
        if self._config_get("untaped.filter") != value:
            self._git(["config", "untaped.filter", value])

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
        revisions = [f"{tree}^{{tree}}" for tree in trees]
        for item in history:
            if item.startswith("-"):
                raise self._error(f"{item!r} is not a revision", category=ErrorCategory.INVALID)
            revisions.append(f"--glob={item}" if "*" in item else item)
        for oid in blobs:
            if not _OBJECT_ID.fullmatch(oid):
                raise self._error(f"{oid!r} is not a blob id", category=ErrorCategory.INVALID)
        wanted = list(dict.fromkeys(blobs))
        if not revisions and not wanted:
            return
        with self._locked():
            self._ensure()
            self._check_ids(wanted)
            progress.update(phase="listing missing blobs")
            missing = self._missing_in(revisions, paths, wanted)
            if not missing:
                return
            progress.update(phase="fetching blobs", done=0, total=len(missing))
            for start in range(0, len(missing), PREFETCH_BATCH_SIZE):
                batch = missing[start : start + PREFETCH_BATCH_SIZE]
                self._prefetch_batch(batch)
                progress.update(phase="fetching blobs", done=start + len(batch), total=len(missing))
            left = self._missing_in(revisions, paths, wanted)
            if left:
                host = url_host(self._url) or self._url
                raise self._fail(
                    GitCommandError(
                        f"{len(left)} blobs still missing after a prefetch from {host}",
                        category=ErrorCategory.UNAVAILABLE,
                        hint=_REFUSED_HINT,
                    )
                )
            self._maintain()

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

    def _check_ids(self, blobs: Sequence[str]) -> None:
        """Refuse an id of the other object format than this repo's (``invalid``)."""
        if not blobs:
            return
        length = _ID_LENGTH.get(self._config_get("extensions.objectformat") or "sha1", 40)
        for oid in blobs:
            if len(oid) != length:
                raise self._error(f"{oid!r} is not a blob id", category=ErrorCategory.INVALID)

    def _missing_blobs(self, blobs: Sequence[str]) -> list[str]:
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
        result = self._git(
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
            env=_NO_LAZY,
        )
        present = {line for line in result.text.splitlines() if not line.startswith("?")}
        return [oid for oid in blobs if oid not in present]

    def _missing_in(
        self, revisions: Sequence[str], paths: Sequence[str], blobs: Sequence[str]
    ) -> list[str]:
        """The blobs missing from ``revisions`` (limited to ``paths``), then from ``blobs``."""
        missing = self._missing(revisions, paths) if revisions else []
        return list(dict.fromkeys([*missing, *self._missing_blobs(blobs)]))

    def _prefetch_batch(self, oids: Sequence[str]) -> None:
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
            self._network(
                argv,
                stdin="".join(f"{oid}\n" for oid in oids),
                timeout=SLOW_TIMEOUT,
                retry=True,
                config={"fetch.negotiationAlgorithm": "noop"},
                raw=True,
            )
        except GitCommandError as exc:
            host = url_host(self._url) or self._url
            lowered = exc.stderr.lower()
            if any(marker in lowered for marker in _REFUSED):
                raise self._fail(
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
            raise self._fail(exc) from exc

    def _missing(self, revisions: Sequence[str], paths: Sequence[str] = ()) -> list[str]:
        result = self._git(
            [
                "rev-list",
                "--objects",
                "--missing=print",
                "--no-object-names",
                *revisions,
                "--",
                *paths,
            ],
            capture=True,
            timeout=SLOW_TIMEOUT,
        )
        return [line[1:] for line in result.text.splitlines() if line.startswith("?")]

    def ls_tree(self, tree: str, paths: Sequence[str] = ()) -> list[TreeEntry]:
        """The entries of ``tree`` (recursive), limited to ``paths``; trees are always present."""
        result = self._git(
            ["ls-tree", "-r", "-z", "--full-tree", tree, "--", *paths], capture=True, env=_NO_LAZY
        )
        entries = []
        for record in result.stdout.split(b"\0"):
            if not record:
                continue
            meta, _, name = record.decode("utf-8", errors="surrogateescape").partition("\t")
            mode, kind, oid = meta.split(" ", 2)
            entries.append(TreeEntry(mode=mode, type=kind, oid=oid, path=name))
        return entries

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
        return self._git(
            argv,
            capture=capture,
            check=check,
            stdin=stdin,
            timeout=timeout,
            env=_NO_LAZY,
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
        if lock_reason is not None:
            worktree_config.check_reason(self, lock_reason)
        with self._locked():
            self._ensure()
            self.prefetch(trees=[ref])
            worktree_config.enable_worktree_config(self)
            local = ref.removeprefix("refs/heads/")
            if branch is not None:
                argv = ["-b", branch, str(path), ref]
            elif ref.startswith("refs/heads/") and self._has_ref(ref):
                argv = [str(path), local]  # resume a branch the repo keeps
            else:
                argv = ["--detach", str(path), ref]
            self._git(["worktree", "add", "--quiet", *argv], env=_NO_LAZY)
            worktree_config.write_owner_config(self, path)
            if branch is not None and ref.startswith("refs/remotes/origin/") and self._plain:
                # The refspec that makes origin/<x> a tracking ref exists only now.
                self._git(["branch", "--quiet", f"--set-upstream-to={ref}"], cwd=path)
            if lock_reason is not None:
                worktree_config.lock_worktree(self, path, reason=lock_reason)

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
        with self._locked():
            worktree_config.lock_worktree(self, worktree, reason=reason)

    def repair_worktree(self, worktree: Path) -> None:
        """Reconnect a worktree moved to ``worktree`` (``git worktree repair``).

        The repo's entry is pointed at the new directory, and the owner stamp,
        which records the absolute path, is rewritten when this plugin owns
        it, also when git already repointed the entry (a hand ``git worktree
        repair`` or ``move``), and so is untaped's credential helper line,
        whose nonce is the folder's. Safe to repeat. Refused when ``worktree`` is
        not one of this repo's worktrees.
        """
        with self._locked():
            worktree_config.repair_worktree(self, worktree)

    def worktrees_under(self, path: Path) -> list[Path]:
        """This plugin's worktrees registered at or under ``path``, sorted.

        Read from each entry's recorded directory, so a folder that was moved
        away or sits on an unmounted drive still lists the worktrees it held.
        """
        root = Path(os.path.realpath(path.expanduser()))
        return sorted(
            entry.path
            for entry in worktree_entries(self._path)
            if entry.owner == self._plugin
            and entry.path is not None
            and entry.path.is_relative_to(root)
        )

    def checkout(self, worktree: Path, ref: str) -> None:
        """Check ``ref`` out in one of this repo's worktrees, its blobs prefetched first."""
        with self._locked():
            worktree_config.check_worktree(self, worktree)
            self.prefetch(trees=[ref])
            self._git(["checkout", "--quiet", ref], cwd=worktree, env=_NO_LAZY)

    def write_worktree_config(self, worktree: Path) -> None:
        """Write the worktree's own config: owner, refspec, filter, URL and credential helper.

        Workspace calls it at create and on every ``status --fetch``: a
        user's ``git fetch``/``push`` there uses this handle's URL
        (``insteadOf`` over the store's label) and, for https, asks
        ``untaped git credential`` after the user's own helpers (instead of
        them with ``git.untaped_helper_only``). The helper line names no
        profile: the folder's project and ``UNTAPED_PROFILE`` pick it, as
        for any untaped command run there.
        """
        worktree_config.write_worktree_config(self, worktree)

    # ── internals ──────────────────────────────────────────────────────────

    @contextmanager
    def _locked(self) -> Iterator[None]:
        with repo_lock(self._path, timeout=LOCK_TIMEOUT, error=self._lock_error):
            yield

    def _lock_error(self, message: str) -> UntapedError:
        return self._error(message)

    def _namespace(self) -> dict[str, str]:
        result = self._git(
            ["for-each-ref", "--format=%(objectname) %(refname)", *self._layout.roots],
            capture=True,
        )
        refs: dict[str, str] = {}
        for line in result.text.splitlines():
            oid, _, ref = line.partition(" ")
            relative = self._layout.relative(ref)
            if relative is not None:
                refs[relative] = oid
        return refs

    def _delete(self, refs: Iterable[str]) -> None:
        lines = "".join(f"delete {ref}\n" for ref in refs)
        if lines:
            self._git(["update-ref", "--no-deref", "--stdin"], stdin=lines)

    def _config_list(self) -> dict[str, list[str]]:
        result = self._git(["config", "--local", "--list", "-z"], capture=True)
        values: dict[str, list[str]] = {}
        for entry in result.stdout.split(b"\0"):
            if entry:
                key, _, value = entry.decode(errors="replace").partition("\n")
                values.setdefault(key.lower(), []).append(value)
        return values

    def _config_get(self, key: str) -> str | None:
        result = self._git(["config", "--local", "--get", key], capture=True, check=False)
        return result.text.strip() if result.returncode == 0 else None

    def _maintain(self) -> None:
        """Git's own automatic maintenance, as the store's second command (a warning on failure)."""
        try:
            result = run_git(
                # The gc task alone, whatever the git's default strategy (newer
                # gits default to geometric repacks, which the gc.* policy doesn't steer).
                ["maintenance", "run", "--auto", "--quiet", "--task=gc"],
                git_dir=self._path,
                cwd=self._path,
                timeout=MAINTENANCE_TIMEOUT,
                capture=False,
                check=False,
                ceiling=True,
            )
        except GitCommandError as exc:
            self._warn(f"maintenance of {self._path} stopped: {exc}; the fetched data is in place")
            return
        if result.returncode != 0:
            gist = result.stderr.strip().splitlines()[-1:] or ["no stderr"]
            self._warn(
                f"maintenance of {self._path} failed: {gist[0]}; the fetched data is in place"
            )

    def _remove_stale_leftovers(self) -> None:
        """Delete what an interrupted fetch, repack or removal left.

        Temporary packs and ``shallow.lock`` older than an hour only, so a git
        running outside untaped keeps its own; a ``<repo>.git.removing``
        always (only the store makes one, under the repo lock).
        """
        store_release.remove_removing(self)
        cutoff = time.time() - _STALE_LEFTOVER_SECONDS
        candidates = [self._path / "shallow.lock"]
        try:
            with os.scandir(self._path / "objects" / "pack") as scan:
                candidates.extend(
                    Path(e.path) for e in scan if e.name.startswith(("tmp_", ".tmp-"))
                )
        except OSError:
            pass
        for path in candidates:
            try:
                if path.is_file(follow_symlinks=False) and path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue

    def _host_auth(self) -> HostAuth | None:
        if self._resolved is None:
            self._resolved = (None if self._auth is None else self._auth(self._url),)
        return self._resolved[0]

    def _network(
        self,
        argv: Sequence[str],
        *,
        capture: bool = False,
        stdin: str | None = None,
        timeout: float = TIMEOUT,
        retry: bool = True,
        config: Mapping[str, str] | None = None,
        raw: bool = False,
    ) -> GitResult:
        """A command that reaches the remote: URL rewrite, credentials, proxy, no maintenance."""
        settings = dict(_NETWORK)
        label = repo_origin(self._path)
        if label and label != self._url:
            settings[f"url.{self._url}.insteadOf"] = label
        secret, proxy = host_config(self._url, self._host_auth())
        settings.update(proxy)
        settings.update(config or {})
        try:
            return run_git(
                argv,
                git_dir=self._path,
                cwd=self._path,
                timeout=timeout,
                capture=capture,
                stdin=stdin,
                auth_config=secret,
                ceiling=True,
                retry_transient=retry,
                attempts=ATTEMPTS,
                sleep=self._sleep,
                config=settings,
            )
        except GitCommandError as exc:
            if raw:
                raise
            raise self._fail(exc) from exc

    def _git(
        self,
        argv: Sequence[str],
        *,
        capture: bool = False,
        check: bool = True,
        stdin: bytes | str | None = None,
        timeout: float | None = None,
        env: Mapping[str, str] | None = None,
        cwd: Path | None = None,
        bare: bool = True,
        locale_c: bool = True,
    ) -> GitResult:
        """A local command on the repo (or, with ``cwd``, in one of its worktrees)."""
        if cwd is not None:
            folder, git_dir = cwd, None
        elif bare:
            folder, git_dir = self._path, self._path
        else:
            folder, git_dir = self._path.parent, None
        try:
            return run_git(
                argv,
                cwd=folder,
                git_dir=git_dir,
                timeout=TIMEOUT if timeout is None else timeout,
                capture=capture,
                check=check,
                stdin=stdin,
                ceiling=True,
                batch_ssh=False,
                env=env,
                locale_c=locale_c,
            )
        except GitCommandError as exc:
            raise self._fail(exc) from exc

    def _fail(self, exc: GitCommandError) -> UntapedError:
        """The caller's error for a git failure: the floor first, then git's own category."""
        version = git_version()
        if below_floor(version) and version is not None:
            exc = GitCommandError(
                f"git {version_text(version)} is older than {floor_text()}, which the repo "
                f"store needs: {exc}",
                returncode=exc.returncode,
                stderr=exc.stderr,
                category=ErrorCategory.CONFIG,
                hint=f"install git {floor_text()} or newer",
            )
        elif exc.category == ErrorCategory.FAILED and _LAZY_READ.search(exc.stderr.lower()):
            exc.hint = _LAZY_HINT
        if self._map_error is not None:
            return self._map_error(exc)
        return self._error(str(exc), **attribution(exc))


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


def record_default_branch(repo: Path, branch: str) -> None:
    """Record ``branch`` as ``repo``'s remote default (``untaped.defaultBranch``) if it exists.

    Best effort, under the repo's lock: a busy or unwritable repo only means
    its next fetch asks the remote itself.
    """
    if not (repo / "HEAD").is_file():
        return
    try:
        # Try once: when another writer holds the repo, its next fetch asks the remote itself.
        with repo_lock(repo, timeout=0, error=UntapedError):
            run_git(
                ["config", "--file", str(repo / "config"), "untaped.defaultBranch", branch],
                cwd=repo,
                timeout=TIMEOUT,
                ceiling=True,
                batch_ssh=False,
            )
    except UntapedError:  # GitCommandError included
        return


def parse_symref(text: str) -> str | None:
    """The branch of ``ref: refs/heads/<b>\\tHEAD`` in ``ls-remote --symref`` output."""
    for line in text.splitlines():
        if line.startswith("ref: refs/heads/") and line.endswith("\tHEAD"):
            return line.removeprefix("ref: refs/heads/").removesuffix("\tHEAD")
    return None


def host_config(url: str, auth: HostAuth | None) -> tuple[dict[str, str], dict[str, str]]:
    """What a command reaching ``url`` gets: ``run_git``'s ``auth_config`` and ``config``.

    ``auth``'s credential and untaped's proxy (``untaped.http.proxy``), both
    scoped to ``url``'s https origin; an ssh URL gets neither (its
    credentials are the user's own).
    """
    origin = https_origin(url)
    if origin is None:
        return {}, {}
    credential = None if auth is None else auth.credential
    secret = {} if credential is None else _credential_config(origin, credential)
    verified = credential is None or credential.verify_tls is not False
    found = http_of(GitSettings).proxy_for(url, verified=verified)
    proxy = {f"http.{origin}/.proxy": found} if found else {}
    return secret, proxy


def _credential_config(origin: str, credential: Credential) -> dict[str, str]:
    """The command-scope settings that send ``credential`` to ``origin`` only (``run_git``'s
    ``auth_config``: a private include file, never argv or the environment).

    The CA file git checks ``origin`` against goes with it, when the
    credential names one; otherwise git verifies as it would.
    """
    header = basic_header(credential.username, credential.password)
    found = {f"http.{origin}/.extraHeader": header}
    if isinstance(credential.verify_tls, Path):
        found[f"http.{origin}/.sslCAInfo"] = str(credential.verify_tls)
    return found


def basic_header(username: str, password: object) -> str:
    """The transient ``AUTHORIZATION`` header for HTTP basic credentials."""
    secret = getattr(password, "get_secret_value", lambda: str(password))()
    token = base64.b64encode(f"{username}:{secret}".encode()).decode()
    return f"AUTHORIZATION: basic {token}"


def _plugin_name(plugin: object) -> str:
    name = getattr(plugin, "name", plugin)
    if not isinstance(name, str):
        raise TypeError("plugin= takes the caller's plugin: its name, or what names it")
    return name
