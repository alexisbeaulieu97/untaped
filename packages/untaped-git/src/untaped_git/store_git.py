"""One store repo's git, as one plugin sees it: the commands every part of the store runs.

:class:`StoreGit` holds what a :class:`~untaped_git.store.RepoStore` handle
was built with (the repo, its URL, the plugin and its layout, the plugin's
error class, the host lookup, the warning sink, the helper policy and the
retry pause) and runs git on the repo with them: a local command, one that
reaches the remote (URL rewrite, credentials, proxy, no maintenance), the
repo's lock, its config and the plugin's refs, and creating the repo and
keeping it. The store's parts (:mod:`~untaped_git.store_fetch`,
:mod:`~untaped_git.store_prefetch`, :mod:`~untaped_git.store_worktrees`,
:mod:`~untaped_git.worktree_config`, :mod:`~untaped_git.store_release` and
:mod:`~untaped_git.plain_clone`) run git on the repo through it, so none of
them knows how the handle is built; only
:func:`~untaped_git.store_fetch.record_default_branch`, which also serves
``untaped_git.api.default_branch`` without a handle, runs its own.

Maintenance is the store's second command after a fetch or prefetch, under
the same lock with its own timeout; its failure is a warning, never a failed
fetch, because the data landed before it ran.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from untaped.sdk import (
    Diagnostics,
    ErrorCategory,
    System,
    UntapedError,
    attribution,
)
from untaped_git import plain_clone, store_release, worktree_config
from untaped_git.host_auth import host_config
from untaped_git.hosts import HostAuth
from untaped_git.namespace import Layout
from untaped_git.repo_files import repo_origin, worktree_entries
from untaped_git.repo_lock import repo_lock
from untaped_git.run import GitCommandError, GitResult, run_git
from untaped_git.store_release import RELEASE_MARK
from untaped_git.version import below_floor, floor_text, git_version, version_text

_diag = Diagnostics()

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
#: Set on every git the store runs except its fetches and its maintenance.
NO_LAZY = {"GIT_NO_LAZY_FETCH": "1"}
_NETWORK = {"maintenance.auto": "false"}
#: How git reports a read of an object it may not fetch lazily: older gits name
#: the promisor remote, newer ones (2.55) only say an object id is not there.
_LAZY_READ = re.compile(r"lazy fetch|promisor remote|not a valid object name [0-9a-f]{40,64}\b")
_LAZY_HINT = (
    "if this is a file's content, read it through RepoStore.prefetched(): it was never prefetched"
)
_STALE_LEFTOVER_SECONDS = 3600.0


@dataclass(frozen=True)
class StoreGit:
    """One store repo, as one plugin's handle sees it, and the git it runs there.

    ``error`` is the plugin's error class (``map_error`` maps a
    ``GitCommandError`` itself instead); ``auth`` looks the URL's host up once,
    ``warn`` hears a failed maintenance, ``helper_only`` is
    ``git.untaped_helper_only`` and ``sleep`` the pause between retries.
    """

    path: Path
    url: str
    plugin: str
    layout: Layout
    error: type[UntapedError]
    map_error: Callable[[GitCommandError], UntapedError] | None = None
    auth: Callable[[str], HostAuth | None] | None = None
    warn: Callable[[str], None] = _diag.warning
    helper_only: bool = False
    sleep: Callable[[float], None] = time.sleep

    def exists(self) -> bool:
        return (self.path / "HEAD").is_file()

    @cached_property
    def host_auth(self) -> HostAuth | None:
        """The URL's host credentials and proxy, looked up once."""
        return None if self.auth is None else self.auth(self.url)

    # ── the repo's lock, config and refs ──────────────────────────────────

    @contextmanager
    def locked(self) -> Iterator[None]:
        with repo_lock(self.path, timeout=LOCK_TIMEOUT, error=self._lock_error):
            yield

    def _lock_error(self, message: str) -> UntapedError:
        return self.error(message)

    def namespace(self) -> dict[str, str]:
        """This plugin's refs, ``heads/<branch>``/``tags/<tag>`` → oid."""
        result = self.git(
            ["for-each-ref", "--format=%(objectname) %(refname)", *self.layout.roots],
            capture=True,
        )
        refs: dict[str, str] = {}
        for line in result.text.splitlines():
            oid, _, ref = line.partition(" ")
            relative = self.layout.relative(ref)
            if relative is not None:
                refs[relative] = oid
        return refs

    def delete(self, refs: Iterable[str]) -> None:
        lines = "".join(f"delete {ref}\n" for ref in refs)
        if lines:
            self.git(["update-ref", "--no-deref", "--stdin"], stdin=lines)

    def has_ref(self, ref: str) -> bool:
        verify = ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"]
        return self.git(verify, check=False).returncode == 0

    def missing(self, revisions: Sequence[str], paths: Sequence[str] = ()) -> list[str]:
        """The blobs ``revisions`` reach (limited to ``paths``) that the repo does not hold."""
        result = self.git(
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

    def config_list(self) -> dict[str, list[str]]:
        result = self.git(["config", "--local", "--list", "-z"], capture=True)
        values: dict[str, list[str]] = {}
        for entry in result.stdout.split(b"\0"):
            if entry:
                key, _, value = entry.decode(errors="replace").partition("\n")
                values.setdefault(key.lower(), []).append(value)
        return values

    def config_get(self, key: str) -> str | None:
        result = self.git(["config", "--local", "--get", key], capture=True, check=False)
        return result.text.strip() if result.returncode == 0 else None

    # ── lifecycle ──────────────────────────────────────────────────────────

    def ensure(self) -> bool:
        """Create the repo when missing and reconcile its policy; ``True`` when created.

        Every writer that may create the repo calls it first under the lock,
        so the next of them also finishes an interrupted release. A
        plain-clone handle records its plugin as the layout's holder here, or
        is refused when another plugin holds it.
        """
        store_release.remove_removing(self)
        marked = self.config_get(RELEASE_MARK) if self.exists() else None
        if marked is not None:
            # An interrupted release: finish it before anything else uses the repo.
            store_release.finish_release(self, marked, ())
            store_release.settle(self)
        created = not self.exists()
        if created:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise self.error(
                    f"could not create repo store directory {self.path.parent}: "
                    f"{exc.strerror or exc}",
                    category=ErrorCategory.FAILED,
                    system=System.LOCAL,
                ) from exc
            # An empty template keeps a user's template hooks out of store repos.
            self.git(["init", "--bare", "--quiet", "--template=", str(self.path)], bare=False)
        current = self.config_list()
        # Checked on every call, so a creation interrupted after ``init`` heals.
        if "remote.origin.url" not in current:
            self.git(["config", "remote.origin.url", self.url])
        for key, value in POLICY.items():
            if current.get(key.lower()) != [value]:
                self.git(["config", "--local", "--replace-all", key, value])
        if self.layout.plain:
            plain_clone.claim(self)
        if "remote.origin.fetch" in current:
            # A 10.x repo's shared refspec: each worktree carries its own now.
            self.git(["config", "--local", "--unset-all", "remote.origin.fetch"])
            if worktree_entries(self.path):
                worktree_config.enable_worktree_config(self)
                worktree_config.repair_worktrees(self, stripped=True)
        return created

    def maintain(self) -> None:
        """Git's own automatic maintenance, as the store's second command (a warning on failure)."""
        try:
            result = run_git(
                # The gc task alone, whatever the git's default strategy (newer
                # gits default to geometric repacks, which the gc.* policy doesn't steer).
                ["maintenance", "run", "--auto", "--quiet", "--task=gc"],
                git_dir=self.path,
                cwd=self.path,
                timeout=MAINTENANCE_TIMEOUT,
                capture=False,
                check=False,
                ceiling=True,
            )
        except GitCommandError as exc:
            self.warn(f"maintenance of {self.path} stopped: {exc}; the fetched data is in place")
            return
        if result.returncode != 0:
            gist = result.stderr.strip().splitlines()[-1:] or ["no stderr"]
            self.warn(f"maintenance of {self.path} failed: {gist[0]}; the fetched data is in place")

    def remove_stale_leftovers(self) -> None:
        """Delete what an interrupted fetch, repack or removal left.

        Temporary packs and ``shallow.lock`` older than an hour only, so a git
        running outside untaped keeps its own; a ``<repo>.git.removing``
        always (only the store makes one, under the repo lock).
        """
        store_release.remove_removing(self)
        cutoff = time.time() - _STALE_LEFTOVER_SECONDS
        candidates = [self.path / "shallow.lock"]
        try:
            with os.scandir(self.path / "objects" / "pack") as scan:
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

    # ── running git ────────────────────────────────────────────────────────

    def network(
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
        """A command that reaches the remote: URL rewrite, credentials, proxy, no maintenance.

        ``raw`` raises git's own :class:`GitCommandError`, for a caller that
        reads it before :meth:`fail`.
        """
        settings = dict(_NETWORK)
        label = repo_origin(self.path)
        if label and label != self.url:
            settings[f"url.{self.url}.insteadOf"] = label
        secret, proxy = host_config(self.url, self.host_auth)
        settings.update(proxy)
        settings.update(config or {})
        try:
            return run_git(
                argv,
                git_dir=self.path,
                cwd=self.path,
                timeout=timeout,
                capture=capture,
                stdin=stdin,
                auth_config=secret,
                ceiling=True,
                retry_transient=retry,
                attempts=ATTEMPTS,
                sleep=self.sleep,
                config=settings,
            )
        except GitCommandError as exc:
            if raw:
                raise
            raise self.fail(exc) from exc

    def git(
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
            folder, git_dir = self.path, self.path
        else:
            folder, git_dir = self.path.parent, None
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
            raise self.fail(exc) from exc

    def fail(self, exc: GitCommandError) -> UntapedError:
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
        if self.map_error is not None:
            return self.map_error(exc)
        return self.error(str(exc), **attribution(exc))
