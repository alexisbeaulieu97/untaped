"""The repo store: one blobless, full-history bare repository per URL, shared by plugins.

Layout: ``<git.store_dir>/<host>/<path>.git``, keyed by
:func:`~untaped_git.domain.url.store_key`, so the https and ssh URLs of one
repo are one directory. Workspace, github's sweep and ansible keep their refs
in the same repo, each in its own namespace
(:mod:`untaped_git.domain.namespace`), and the store holds the same thing for
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
credentials (from :class:`~untaped_git.domain.hosts.GitHost`) are scoped to
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
import shlex
import shutil
import sys
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Self

from untaped.sdk import (
    ErrorCategory,
    GitCommandError,
    GitResult,
    UntapedError,
    attribution,
    run_git,
    ui_context,
)
from untaped_git.domain.delta import RefDelta, diff_refs
from untaped_git.domain.hosts import Credential, HostAuth
from untaped_git.domain.namespace import (
    ORIGIN_HEAD,
    check_names,
    covered,
    is_glob,
    layout_for,
)
from untaped_git.domain.records import TreeEntry
from untaped_git.domain.release import Released, Removed
from untaped_git.domain.url import https_origin, url_host
from untaped_git.infrastructure.lock import repo_lock
from untaped_git.infrastructure.repo_files import (
    WorktreeEntry,
    list_repos,
    private_file,
    private_files,
    repo_origin,
    tree_size,
    worktree_entries,
)
from untaped_git.infrastructure.version import below_floor, floor_text, git_version, version_text

#: Explicit refs fetched per command: a dropped connection loses one batch.
DEFAULT_FETCH_BATCH_SIZE = 50
#: Object ids per prefetch command (``fetch --stdin``).
PREFETCH_BATCH_SIZE = 5000
TIMEOUT = 60.0
SLOW_TIMEOUT = 600.0
MAINTENANCE_TIMEOUT = 600.0
LOCK_TIMEOUT = 600.0
ATTEMPTS = 3
#: The layout mark; ``migrate-dirs`` sets it on moved repos.
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
#: The mark ``release()`` writes before deleting anything; ``ensure()`` finishes from it.
RELEASE_MARK = "untaped.release"
#: Where a repo nobody holds goes, atomically, before it is deleted.
REMOVING_SUFFIX = ".removing"
#: Refs that are some user's data: a repo holding any stays on release.
_HELD_ROOTS = ("refs/untaped/", "refs/remotes/", "refs/heads/", "refs/tags/", "refs/stash")
_PLAIN_REFSPEC = "+refs/heads/*:refs/remotes/origin/*"
_HANDLE_REFUSED = ("fetch", "checkout", "worktree")


class RepoStore:
    """One repo of the store, as one plugin sees it; see the module docstring.

    Build it with :meth:`for_url`. Writers (``ensure``, ``fetch``,
    ``prefetch`` and what calls it, ``delete_refs``,
    ``write_worktree_config``) hold the repo's lock; ``refs``, ``ls_tree``
    and a handle's ``run`` only read and do not.
    """

    def __init__(
        self,
        path: Path,
        *,
        url: str,
        plugin: str,
        error: type[UntapedError],
        map_error: Callable[[GitCommandError], UntapedError] | None = None,
        auth: Callable[[str], HostAuth | None] | None = None,
        warn: Callable[[str], None] | None = None,
        helper_first: bool = False,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._path = path
        self._url = url
        self._plugin = plugin
        self._layout = layout_for(plugin)
        self._plain = self._layout.heads == "refs/remotes/origin/"
        self._error = error
        self._map_error = map_error
        self._auth = auth
        self._resolved: tuple[HostAuth | None] | None = None
        self._warn = warn or _warn
        self._helper_first = helper_first
        self._sleep = sleep

    @classmethod
    def for_url(
        cls,
        url: str,
        *,
        plugin: object,
        error: type[UntapedError],
        map_error: Callable[[GitCommandError], UntapedError] | None = None,
    ) -> Self:
        """The store repo of ``url`` for ``plugin`` (its ``PluginSpec``) under ``git.store_dir``.

        ``error`` is the plugin's error class: every failure is raised as one,
        keeping git's category (``map_error`` maps a ``GitCommandError``
        itself instead).
        """
        from untaped_git.domain.hosts import resolve_host  # noqa: PLC0415  # contracts load lazily
        from untaped_git.domain.url import store_key  # noqa: PLC0415
        from untaped_git.settings import git_settings  # noqa: PLC0415

        settings = git_settings()
        return cls(
            settings.store_dir.expanduser().joinpath(*store_key(url)),
            url=url,
            plugin=_plugin_name(plugin),
            error=error,
            map_error=map_error,
            auth=resolve_host,
            helper_first=settings.untaped_helper_first,
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
        from untaped_git.domain.hosts import resolve_host  # noqa: PLC0415  # contracts load lazily
        from untaped_git.settings import git_settings  # noqa: PLC0415

        name = _plugin_name(plugin)
        settings = git_settings()
        stores = []
        for repo in list_repos(settings.store_dir.expanduser()):
            label = repo_origin(repo)
            if label is None or not private_file(repo, name).is_file():
                continue
            stores.append(
                cls(
                    repo,
                    url=label,
                    plugin=name,
                    error=error,
                    map_error=map_error,
                    auth=resolve_host,
                    helper_first=settings.untaped_helper_first,
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

    def exists(self) -> bool:
        return (self._path / "HEAD").is_file()

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

    def ensure(self) -> bool:
        """Create the repo when missing and reconcile its policy; ``True`` when created."""
        with self._locked():
            return self._ensure()

    def _ensure(self) -> bool:
        self._remove_removing()
        marked = self._config_get(RELEASE_MARK) if self.exists() else None
        if marked is not None:
            # An interrupted release: finish it before anything else uses the repo.
            self._finish_release(marked, ())
            self._settle()
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
                self._enable_worktree_config()
                self._repair_worktrees(stripped=True)
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
        the next :meth:`ensure` finish a release that was interrupted, and
        another plugin's interrupted release is finished first.

        ``unless_in_use`` changes nothing while one of this plugin's worktrees
        is registered, checked under the repo lock (:class:`Released` then
        counts it), so a worktree another process just added is never deleted.
        """
        refs = []
        for name in branches:
            reason = check_names([name])
            if reason is None and is_glob(name):
                reason = f"{name!r} is a glob; release takes exact branch names"
            if reason is not None:
                raise self._error(reason, category=ErrorCategory.INVALID)
            refs.append(f"refs/heads/{name}")
        with self._locked():
            if not self.exists():
                self._remove_removing()
                return Removed()
            if unless_in_use and any(
                entry.owner == self._plugin and entry.path is not None and entry.path.is_dir()
                for entry in worktree_entries(self._path)
            ):
                return self._holders() or Released()
            checked_out = set(self._checked_out()) & set(refs)
            if checked_out:
                names = ", ".join(sorted(ref.removeprefix("refs/heads/") for ref in checked_out))
                raise self._error(
                    f"cannot release {self._path}: a worktree has {names} checked out",
                    category=ErrorCategory.CONFLICT,
                )
            marked = self._config_get(RELEASE_MARK)
            if marked is not None and marked != self._plugin:
                # Another plugin's interrupted release: finish it before the mark is reused.
                self._finish_release(marked, ())
            self._git(["config", RELEASE_MARK, self._plugin])
            self._finish_release(self._plugin, refs)
            return self._settle()

    def _finish_release(self, plugin: str, branches: Sequence[str]) -> None:
        """Delete ``plugin``'s refs, private file and worktrees, and ``branches``."""
        layout = layout_for(plugin)
        refs = self._git(
            ["for-each-ref", "--format=%(refname)", *layout.roots], capture=True
        ).text.split()
        if layout.heads == "refs/remotes/origin/":
            refs.append(ORIGIN_HEAD)
        self._delete(dict.fromkeys([*refs, *branches]))
        private_file(self._path, plugin).unlink(missing_ok=True)
        for entry in worktree_entries(self._path):
            if entry.owner == plugin:
                self._remove_worktree(entry)
        self._git(["worktree", "prune"], check=False)

    def _remove_worktree(self, entry: WorktreeEntry) -> None:
        if entry.path is not None:
            removed = self._git(
                ["worktree", "remove", "--force", "--force", str(entry.path)], check=False
            )
            if removed.returncode == 0:
                return
            shutil.rmtree(entry.path, ignore_errors=True)
        shutil.rmtree(entry.admin, ignore_errors=True)

    def _settle(self) -> Released | Removed:
        """Clear the mark when someone still holds the repo, else remove it."""
        released = self._holders()
        if released is not None:
            self._git(["config", "--unset-all", RELEASE_MARK], check=False)
            return released
        freed = tree_size(self._path)
        removing = self._removing_path()
        self._remove_removing()
        try:
            self._path.rename(removing)
        except OSError as exc:
            raise self._error(
                f"could not remove repo store directory {self._path}: {exc.strerror or exc}",
                category=ErrorCategory.FAILED,
                system="local",
            ) from exc
        shutil.rmtree(removing, ignore_errors=True)
        return Removed(freed)

    def _holders(self) -> Released | None:
        """Who still holds the repo, or ``None`` when nobody does."""
        refs = self._git(
            ["for-each-ref", "--format=%(refname)", *_HELD_ROOTS], capture=True
        ).text.split()
        plugins: dict[str, int] = dict.fromkeys(private_files(self._path), 0)
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
        for entry in worktree_entries(self._path):
            if entry.owner is None:
                foreign.append(_shown_path(entry.path) if entry.path else str(entry.admin))
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

    def _checked_out(self) -> list[str]:
        """The branches registered worktrees have checked out (``worktree list``)."""
        result = self._git(["worktree", "list", "--porcelain"], capture=True, check=False)
        return [
            line.removeprefix("branch ")
            for line in result.text.splitlines()
            if line.startswith("branch ")
        ]

    def _removing_path(self) -> Path:
        return self._path.with_name(self._path.name + REMOVING_SUFFIX)

    def _remove_removing(self) -> None:
        """Delete what an interrupted removal left beside the repo."""
        removing = self._removing_path()
        if removing.is_dir() and not removing.is_symlink():
            shutil.rmtree(removing, ignore_errors=True)

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
                stderr = self._fetch_globs(branches, tags, prune=prune)
            else:
                stderr = self._fetch_listed(branches, tags, prune=prune, before=before)
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
    ) -> str:
        layout = self._layout
        remote = self._list_remote(heads=bool(branches), tags=bool(tags))
        wanted = [
            ref
            for ref, oid in remote.items()
            if covered(ref, branches, tags) and before.get(ref) != oid
        ]
        stderr: list[str] = []
        for start in range(0, len(wanted), DEFAULT_FETCH_BATCH_SIZE):
            batch = wanted[start : start + DEFAULT_FETCH_BATCH_SIZE]
            specs = []
            for ref in batch:
                kind, _, name = ref.partition("/")
                specs.extend(layout.refspecs(kind, [name]))
            stderr.append(self._fetch_refspecs(specs, prune=False))
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
    ) -> None:
        """Fetch, in one round trip per batch, every blob the reads will need.

        ``trees`` are peeled (``<x>^{tree}``: a commit lists its own tree's
        blobs only); ``history`` walks every commit the revisions reach (a
        ``*`` names a ref glob, ``a..b`` a range). ``paths`` limits both. When
        nothing is missing no network call is made. A blob still missing
        afterwards, or a host refusing the fetch, is ``unavailable`` (exit 5).
        """
        revisions = [f"{tree}^{{tree}}" for tree in trees]
        for item in history:
            if item.startswith("-"):
                raise self._error(f"{item!r} is not a revision", category=ErrorCategory.INVALID)
            revisions.append(f"--glob={item}" if "*" in item else item)
        if not revisions:
            return
        with self._locked():
            self._ensure()
            missing = self._missing(revisions, paths)
            if not missing:
                return
            for start in range(0, len(missing), PREFETCH_BATCH_SIZE):
                self._prefetch_batch(missing[start : start + PREFETCH_BATCH_SIZE])
            left = self._missing(revisions, paths)
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

    def prefetched(self, *, trees: Sequence[str], paths: Sequence[str] = ()) -> Prefetched:
        """Prefetch ``trees`` (peeled) limited to ``paths``, then a handle that reads them."""
        self.prefetch(trees=trees, paths=paths)
        return Prefetched(self, tuple(trees), tuple(paths))

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
        a :meth:`prefetched` handle; ``check_conventions`` flags them here.
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

    def worktree_add(self, path: Path, ref: str, *, branch: str | None = None) -> None:
        """Add a worktree at ``path`` on ``ref`` (a new ``branch`` from it, else detached).

        The tip's blobs are prefetched first, so a host that refuses them
        fails before anything is written. The first worktree moves
        ``core.bare`` into the repo's ``config.worktree``.
        """
        with self._locked():
            self._ensure()
            self.prefetch(trees=[ref])
            self._enable_worktree_config()
            local = ref.removeprefix("refs/heads/")
            if branch is not None:
                argv = ["-b", branch, str(path), ref]
            elif ref.startswith("refs/heads/") and self._has_ref(ref):
                argv = [str(path), local]  # resume a branch the repo keeps
            else:
                argv = ["--detach", str(path), ref]
            self._git(["worktree", "add", "--quiet", *argv], env=_NO_LAZY)
            self._write_owner_config(path)
            if branch is not None and ref.startswith("refs/remotes/origin/") and self._plain:
                # The refspec that makes origin/<x> a tracking ref exists only now.
                self._git(["branch", "--quiet", f"--set-upstream-to={ref}"], cwd=path)

    def checkout(self, worktree: Path, ref: str) -> None:
        """Check ``ref`` out in one of this repo's worktrees, its blobs prefetched first."""
        with self._locked():
            self._check_worktree(worktree)
            self.prefetch(trees=[ref])
            self._git(["checkout", "--quiet", ref], cwd=worktree, env=_NO_LAZY)

    def write_worktree_config(self, worktree: Path, *, profile: str | None) -> None:
        """Write the worktree's own config: owner, refspec, filter, URL and credential helper.

        Workspace calls it at create and on every ``status --fetch``: a
        user's ``git fetch``/``push`` there uses this handle's URL
        (``insteadOf`` over the store's label) and, for https, asks
        ``untaped git credential`` with ``profile`` after the user's own
        helpers (instead of them with ``git.untaped_helper_first``).
        """
        with self._locked():
            self._check_worktree(worktree)
            self._enable_worktree_config()
            self._write_owner_config(worktree)
            label = repo_origin(self._path)
            # Every rewrite onto the label goes, so an earlier URL spelling
            # (https before ssh, say) never wins over this one.
            for key in self._rewrites_onto(worktree, label) if label else ():
                self._worktree_config(worktree, "--unset-all", key, check=False)
            if label and label != self._url:
                self._worktree_config(worktree, f"url.{self._url}.insteadOf", label)
            origin = https_origin(self._url)
            if origin is None:
                return
            helper = f"credential.{origin}.helper"
            self._worktree_config(worktree, f"credential.{origin}.useHttpPath", "true")
            self._worktree_config(worktree, "--unset-all", helper, check=False)
            if self._helper_first:
                self._worktree_config(worktree, "--add", helper, "")
            self._worktree_config(worktree, "--add", helper, _helper_command(profile))

    def _enable_worktree_config(self) -> None:
        if self._config_get("extensions.worktreeConfig") == "true":
            return
        self._git(["config", "--file", str(self._path / "config.worktree"), "core.bare", "true"])
        self._git(["config", "--local", "--unset-all", "core.bare"], check=False)
        self._git(["config", "core.repositoryformatversion", "1"])
        self._git(["config", "extensions.worktreeConfig", "true"])
        self._repair_worktrees(stripped=False)

    def _repair_worktrees(self, *, stripped: bool) -> None:
        """Give each registered worktree its own fetch keys after a shared-config change.

        A worktree workspace owns gets workspace's owner keys. With the shared
        refspec just ``stripped``, a worktree nobody owns (a 10.x worktree, or
        one added by hand) gets the refspec and filter it lost, never an
        owner: release would then remove it.
        """
        if self._config_get("extensions.worktreeConfig") != "true":
            return
        for entry in worktree_entries(self._path):
            if entry.path is None or not entry.path.is_dir():
                continue
            if entry.owner == "workspace":
                self._write_owner_config(entry.path, owner="workspace")
            elif entry.owner is None and stripped:
                self._write_fetch_config(entry.path, plain=True)

    def _write_owner_config(self, worktree: Path, *, owner: str | None = None) -> None:
        owner = owner or self._plugin
        self._worktree_config(worktree, "untaped.owner", owner)
        # Stamps which worktree is owned: git copies this file into worktrees
        # added from this one, which must stay a user's own.
        self._worktree_config(worktree, "untaped.worktree", str(worktree.resolve()))
        plain = layout_for(owner).heads == "refs/remotes/origin/"
        self._write_fetch_config(worktree, plain=plain)

    def _write_fetch_config(self, worktree: Path, *, plain: bool) -> None:
        refspec = "remote.origin.fetch"
        self._worktree_config(worktree, "--unset-all", refspec, check=False)
        if plain:
            self._worktree_config(worktree, refspec, _PLAIN_REFSPEC)
        # A user's own fetch here brings the blobs of new commits.
        self._worktree_config(worktree, "remote.origin.partialclonefilter", "")

    def _check_worktree(self, worktree: Path) -> None:
        """Refuse a path that is not one of this repo's worktrees (a user's own clone, say)."""
        result = self._git(
            ["rev-parse", "--git-common-dir"],
            cwd=worktree,
            capture=True,
            check=False,
        )
        common = result.text.strip()
        # Relative to the worktree when git prints it relative (``--path-format`` is 2.31+).
        if result.returncode != 0 or (worktree / common).resolve() != self._path.resolve():
            raise self._error(
                f"{worktree} is not a worktree of repo store {self._path}",
                category=ErrorCategory.INVALID,
            )

    def _rewrites_onto(self, worktree: Path, label: str) -> list[str]:
        result = self._git(
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

    def _worktree_config(self, worktree: Path, *args: str, check: bool = True) -> None:
        self._git(["config", "--worktree", *args], cwd=worktree, check=check)

    # ── report data ────────────────────────────────────────────────────────

    def filter_state(self) -> str | None:
        """``honoured``, ``ignored`` or ``None`` (no fetch has brought anything yet)."""
        return self._config_get("untaped.filter") if self.exists() else None

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
        self._remove_removing()
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
        auth = self._host_auth()
        secret: dict[str, str] = {}
        origin = https_origin(self._url)
        if auth is not None:
            if auth.credential is not None and origin is not None:
                secret = credential_config(origin, auth.credential)
            if auth.proxy and origin is not None:
                settings[f"http.{origin}/.proxy"] = auth.proxy
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
    """A store repo whose blobs for ``trees`` (and ``paths``) are present: read them here.

    :meth:`run` is the only call through which a consumer runs ``grep``,
    ``cat-file``, ``show`` or ``archive`` on a store repo; it exists only
    after its prefetch. It runs unlocked with ``GIT_NO_LAZY_FETCH``, so a read
    outside the prefetched trees and paths fails instead of fetching.
    """

    def __init__(self, store: RepoStore, trees: tuple[str, ...], paths: tuple[str, ...]) -> None:
        self._store = store
        self.trees = trees
        self.paths = paths

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


def credential_config(origin: str, credential: Credential) -> dict[str, str]:
    """The command-scope setting that sends ``credential`` to ``origin`` only (``run_git``'s
    ``auth_config``: a private include file, never argv or the environment)."""
    header = basic_header(credential.username, credential.password)
    return {f"http.{origin}/.extraHeader": header}


def basic_header(username: str, password: object) -> str:
    """The transient ``AUTHORIZATION`` header for HTTP basic credentials."""
    secret = getattr(password, "get_secret_value", lambda: str(password))()
    token = base64.b64encode(f"{username}:{secret}".encode()).decode()
    return f"AUTHORIZATION: basic {token}"


def _helper_command(profile: str | None) -> str:
    executable = shutil.which("untaped") or str(Path(sys.argv[0]).resolve())
    command = f"!{shlex.quote(executable)}"
    if profile:
        command += f" --profile {shlex.quote(profile)}"
    return f"{command} git credential"


def _warn(text: str) -> None:
    ui_context(strict=False).message("warning", text)


def _plugin_name(plugin: object) -> str:
    name = getattr(plugin, "name", plugin)
    if not isinstance(name, str):
        raise TypeError("plugin= takes the caller's PluginSpec")
    return name


def _shown_path(path: Path) -> str:
    """``path`` with the home directory as ``~``."""
    home = Path.home()
    return f"~/{path.relative_to(home)}" if path.is_relative_to(home) else str(path)
