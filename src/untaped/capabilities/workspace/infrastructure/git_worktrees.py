"""Git worktrees over a shared bare cache: one cache per repo URL, many worktrees.

Each repo URL maps to a bare cache (:func:`untaped.sdk.cache_path`) whose
``origin`` remote fetches into ``refs/remotes/origin/*``; workspaces get
``git worktree`` checkouts from it. Every cache write runs under the cache's
advisory lock, so parallel provisioning of one repo serializes safely. Git
runs on a cache go through :class:`untaped.sdk.RepoCache`, and runs in a
worktree through ``run_git``; failures become :class:`GitError` keeping git's
attribution, and a branch or directory that is already in use becomes a
``conflict``. :meth:`LocalGitWorktrees.cached_repos`
lists the caches without running git (the repo picker opens on it).
"""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from pathlib import Path

from untaped.capabilities.workspace.domain.models import CachedRepo, Checkout, WorktreeStatus
from untaped.capabilities.workspace.domain.safety import archive_blockers
from untaped.capabilities.workspace.errors import GitError, WorkspaceError
from untaped.sdk import (
    GitCommandError,
    GitResult,
    RepoCache,
    attribution,
    cache_origin,
    cache_path,
    list_caches,
    run_git,
)

_FETCH_REFSPEC = "+refs/heads/*:refs/remotes/origin/*"
_LAYOUT_KEY = "untaped.layout"
_LAYOUT = "2"
"""Marks a cache on the 10.x remote-tracking layout; unmarked caches are refused."""
_CACHE_CONFIG = r"^(remote\.origin\.(url|fetch)|untaped\.layout)$"
_STASH_MARKERS = ("refs/stash", "logs/refs/stash", "reftable")
"""Paths under a common git dir whose absence means no stash (reftable: cannot tell)."""
_BRANCH_IN_USE = ("already checked out", "used by worktree")
_BRANCH_IN_USE_HINT = (
    "the branch is checked out in another workspace: archive that workspace or pick another branch"
)
_EXISTS_HINT = "move the existing directory aside, or use another branch name"


class LocalGitWorktrees:
    """:class:`GitWorktrees` backed by bare caches under ``cache_dir``."""

    def __init__(
        self,
        cache_dir: Path,
        *,
        git: str = "git",
        timeout: float = 60.0,
        slow_timeout: float = 600.0,
        lock_timeout: float = 600.0,
    ) -> None:
        self._cache_dir = cache_dir
        self._git = git
        self._timeout = timeout
        self._slow_timeout = slow_timeout
        self._lock_timeout = lock_timeout

    # -- public port -------------------------------------------------------

    def checkout(self, url: str, dest: Path, *, branch: str | None, base: str | None) -> Checkout:
        """Add a worktree at ``dest``: on ``branch``, or detached at the base when ``None``."""
        cache = self._cache(url)
        target = str(dest.absolute())
        with cache.locked():
            self._ensure_cache(cache, url)
            cache.fetch()
            picked, refs = self._base_and_refs(cache, branch, base)
            if f"refs/remotes/origin/{picked}" not in refs:
                raise GitError(
                    f"base branch '{picked}' not found on origin of {url}", category="not_found"
                )
            cache.run(["worktree", "prune"])
            if branch is None:
                cache.run(["worktree", "add", "--detach", target, f"origin/{picked}"])
                return Checkout(
                    action="checked_out", base=picked, detail=f"read-only at origin/{picked}"
                )
            checkout = self._add_writable(cache, target, branch, picked, refs)
            self._set_upstream(cache, branch)
            return checkout

    def status(self, dest: Path, *, branch: str | None, base: str) -> WorktreeStatus | None:
        """Git state of the worktree at ``dest``; ``None`` when it is missing."""
        if not dest.exists():
            return None
        common = self._check_registered(dest)
        out = self._in(dest, ["status", "--porcelain=v2", "--branch"], capture=True).text
        head, upstream, ahead, behind, modified, untracked = _parse_status(out)
        if upstream is not None and not self._has_ref(dest, f"refs/remotes/{upstream}"):
            upstream = None  # configured for the first push, but not on origin yet
        return WorktreeStatus(
            branch=head,
            upstream=upstream,
            ahead=ahead,
            behind=behind,
            modified=modified,
            untracked=untracked,
            stashed=self._stashed(dest, branch, common=common),
            unpushed=self._unpushed(dest),
            submodules=(dest / ".gitmodules").exists() and self._has_submodules(dest),
        )

    def fetch(self, url: str) -> None:
        """Fetch the cache for ``url`` (no-op when it is missing)."""
        cache = self._cache(url)
        if not cache.exists():
            return
        with cache.locked():
            cache.fetch()

    def cache_exists(self, url: str) -> bool:
        return self._cache(url).exists()

    def remote_branches(self, url: str) -> list[str]:
        """Branch names of ``url``'s cache as last fetched, sorted; ``[]`` when it is missing."""
        cache = self._cache(url)
        if not cache.exists():
            return []
        args = ["for-each-ref", "--format=%(refname:lstrip=3)", "refs/remotes/origin"]
        names = cache.run(args, capture=True).text.split()
        return [name for name in names if name != "HEAD"]

    def cached_repos(self) -> list[CachedRepo]:
        """Every bare cache under ``cache_dir``, sorted by :attr:`CachedRepo.ident`; no git runs.

        A cache is a ``<host>/[<owner>/...]<name>.git`` directory, a leaf:
        never entered, so its contents are not scanned. ``_unknown`` (caches of
        host-less URLs, keyed on a hash) is skipped. Each origin is read from
        the cache's ``config`` file rather than by running git per cache.
        """
        root = self._cache_dir.expanduser().resolve()
        found: list[CachedRepo] = []
        for path in list_caches(root, skip=("_unknown",)):
            key = path.relative_to(root).parts
            if len(key) >= 2:
                found.append(CachedRepo(key=key, origin=cache_origin(path)))
        return sorted(found, key=lambda repo: repo.ident)

    def remove(self, url: str, dest: Path, *, force: bool) -> None:
        """Remove the worktree at ``dest`` and prune stale worktree entries."""
        cache = self._cache(url)
        if not cache.exists():
            if not force:
                raise GitError(
                    f"the repo cache for {url} is missing",
                    category="failed",
                    hint="pass --force to delete the directory",
                )
            _delete_tree(dest)
            return
        with cache.locked():
            if dest.exists():
                if not force:
                    self._recheck(cache, dest)
                self._remove_worktree(cache, dest, force=force)
            cache.run(["worktree", "prune"])

    def _recheck(self, cache: RepoCache, dest: Path) -> None:
        """Refuse (``conflict``) when work appeared in ``dest`` since the caller's status check.

        Run under the cache lock right before ``worktree remove``: changes,
        stashes on the checked-out branch, or unpushed commits block it.
        """
        out = self._in(dest, ["status", "--porcelain=v2", "--branch"], capture=True).text
        head, _, _, _, modified, untracked = _parse_status(out)
        status = WorktreeStatus(
            branch=head,
            upstream=None,
            ahead=0,
            behind=0,
            modified=modified,
            untracked=untracked,
            stashed=self._stashed(dest, head, common=cache.path),
            unpushed=self._unpushed(dest),
        )
        blockers = archive_blockers(status)
        if blockers:
            raise GitError(
                f"{dest}: {', '.join(blockers)}; nothing removed",
                category="conflict",
                hint="the repo changed since the check; run status and archive again",
            )

    def _remove_worktree(self, cache: RepoCache, dest: Path, *, force: bool) -> None:
        """``git worktree remove``; forced, a worktree git refuses is deleted outright.

        ``--force`` twice also removes a locked worktree or one with
        submodules. Git still refuses a worktree the cache does not know
        (its cache was recreated) or cannot read; forced, that directory is
        deleted directly.
        """
        flags = ["--force", "--force"] if force else []
        try:
            cache.run(["worktree", "remove", *flags, str(dest.absolute())])
        except GitError:
            if not force:
                raise
            _delete_tree(dest)

    # -- checkout steps ----------------------------------------------------

    def _ensure_cache(self, cache: RepoCache, url: str) -> None:
        """Create the cache, or repair a 10.x one (origin URL, refspec, layout mark).

        An unmarked cache holding ``refs/heads`` is a 9.x mirror and is refused;
        an unmarked one without heads holds nothing fetched (e.g. a crash between
        creation and the mark), so it is adopted and marked.
        """
        config: dict[str, list[str]] = {}
        if cache.exists():
            config = self._cache_config(cache)
            if config.get(_LAYOUT_KEY) != [_LAYOUT] and self._has_heads(cache):
                raise WorkspaceError(
                    f"{cache.path} is a cache from untaped 9.x",
                    hint=(
                        "set workspace.cache_dir to a new directory; keep this one"
                        " while clones made before untaped 7.0 borrow objects from it"
                    ),
                )
        if cache.ensure(url):
            cache.run(["config", _LAYOUT_KEY, _LAYOUT])
            config[_LAYOUT_KEY] = [_LAYOUT]
        if config.get("remote.origin.fetch") != [_FETCH_REFSPEC]:
            cache.run(["config", "--replace-all", "remote.origin.fetch", _FETCH_REFSPEC])
        if config.get(_LAYOUT_KEY) != [_LAYOUT]:
            cache.run(["config", "--replace-all", _LAYOUT_KEY, _LAYOUT])

    def _has_heads(self, cache: RepoCache) -> bool:
        """Whether the cache holds any ``refs/heads/*`` (mirrored heads of a 9.x cache)."""
        heads = cache.run(
            ["for-each-ref", "--count=1", "--format=%(refname)", "refs/heads"], capture=True
        ).text
        return bool(heads.strip())

    def _cache_config(self, cache: RepoCache) -> dict[str, list[str]]:
        """The cache's origin URL and fetch refspecs, and its layout mark, by key."""
        out = cache.run(["config", "--get-regexp", _CACHE_CONFIG], capture=True, check=False).text
        config: dict[str, list[str]] = {}
        for line in out.splitlines():
            key, _, value = line.partition(" ")
            config.setdefault(key, []).append(value)
        return config

    def _worktree_branches(self, cache: RepoCache) -> set[str] | None:
        """Branches checked out in registered worktrees; ``None`` when there are none."""
        out = cache.run(["worktree", "list", "--porcelain"], capture=True).text
        lines = out.splitlines()
        if sum(1 for line in lines if line.startswith("worktree ")) <= 1:
            return None
        return {line.removeprefix("branch ") for line in lines if line.startswith("branch ")}

    def _base_and_refs(
        self, cache: RepoCache, branch: str | None, base: str | None
    ) -> tuple[str, dict[str, str]]:
        """The base (``base``, else origin's default) and :meth:`_refs` for it.

        A default whose ref is gone (origin renamed its default branch, and
        ``fetch --prune`` left ``origin/HEAD`` dangling) is learnt again once.
        """
        picked = base if base is not None else self._default_branch(cache)
        refs = self._refs(cache, branch, picked)
        if base is None and f"refs/remotes/origin/{picked}" not in refs:
            cache.run(["remote", "set-head", "origin", "--auto"], check=False)
            picked = self._origin_head(cache) or "main"
            refs = self._refs(cache, branch, picked)
        return picked, refs

    def _default_branch(self, cache: RepoCache) -> str:
        """origin's default branch (``origin/HEAD``, learnt once if unknown), else ``main``."""
        head = self._origin_head(cache)
        if head is None:
            cache.run(["remote", "set-head", "origin", "--auto"], check=False)
            head = self._origin_head(cache)
        return head or "main"

    def _origin_head(self, cache: RepoCache) -> str | None:
        args = ["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"]
        head = cache.run(args, capture=True, check=False).text.strip()
        return head.removeprefix("origin/") or None

    def _refs(self, cache: RepoCache, branch: str | None, base: str) -> dict[str, str]:
        """Object ids of whichever of the branch (local and origin) and base refs exist."""
        names = [f"refs/remotes/origin/{base}"]
        if branch is not None:
            names += [f"refs/heads/{branch}", f"refs/remotes/origin/{branch}"]
        args = ["for-each-ref", "--format=%(refname) %(objectname)", *names]
        out = cache.run(args, capture=True).text
        pairs = (line.partition(" ") for line in out.splitlines())
        return {ref: oid for ref, _, oid in pairs if ref in names}

    def _add_writable(
        self, cache: RepoCache, target: str, branch: str, base: str, refs: dict[str, str]
    ) -> Checkout:
        local = refs.get(f"refs/heads/{branch}")
        remote = refs.get(f"refs/remotes/origin/{branch}")
        tracking = f"origin/{branch}"
        if local and f"refs/heads/{branch}" in (self._worktree_branches(cache) or set()):
            raise GitError(
                f"branch '{branch}' is already checked out in another worktree",
                category="conflict",
                hint=_BRANCH_IN_USE_HINT,
            )
        if local and remote:
            detail = self._reconcile(cache, branch, same=local == remote)
            cache.run(["worktree", "add", target, branch])
            return Checkout(action="checked_out", base=base, detail=detail)
        if remote:
            cache.run(["worktree", "add", "-b", branch, target, tracking])
            return Checkout(action="checked_out", base=base, detail=f"tracking {tracking}")
        if local:
            cache.run(["worktree", "add", target, branch])
            return Checkout(action="checked_out", base=base, detail="resumed local branch")
        cache.run(["worktree", "add", "-b", branch, target, f"origin/{base}"])
        return Checkout(action="created", base=base, detail=f"from origin/{base}")

    def _reconcile(self, cache: RepoCache, branch: str, *, same: bool) -> str:
        """Fast-forward a local branch behind its remote; describe how the two relate."""
        tracking = f"origin/{branch}"
        if same:
            return f"tracking {tracking}"
        if self._is_ancestor(cache, branch, tracking):
            cache.run(["branch", "-f", branch, tracking])
            return f"tracking {tracking}"
        if self._is_ancestor(cache, tracking, branch):
            return f"resumed; ahead of {tracking}"
        return f"resumed; diverged from {tracking}"

    def _set_upstream(self, cache: RepoCache, branch: str) -> None:
        cache.run(["config", f"branch.{branch}.remote", "origin"])
        cache.run(["config", f"branch.{branch}.merge", f"refs/heads/{branch}"])

    # -- status helpers ----------------------------------------------------

    def _stashed(self, wt: Path, branch: str | None, *, common: Path) -> int:
        """Stash entries made on ``branch``; no git run when ``common`` has no stash."""
        if not any((common / marker).exists() for marker in _STASH_MARKERS):
            return 0
        label = branch if branch is not None else "(no branch)"
        out = self._in(wt, ["stash", "list", "--format=%gs"], capture=True).text
        prefixes = (f"WIP on {label}:", f"On {label}:")
        return sum(1 for line in out.splitlines() if line.startswith(prefixes))

    def _unpushed(self, wt: Path) -> int:
        """Commits on ``HEAD`` that no remote-tracking branch has (detached or not)."""
        args = ["rev-list", "--count", "HEAD", "--not", "--remotes"]
        out = self._in(wt, args, capture=True).text
        return int(out.strip() or 0)

    def _has_submodules(self, wt: Path) -> bool:
        """Whether any submodule is initialised (``-`` marks an uninitialised one)."""
        out = self._in(wt, ["submodule", "status", "--recursive"], capture=True).text
        return any(line and not line.startswith("-") for line in out.splitlines())

    def _check_registered(self, dest: Path) -> Path:
        """Raise unless ``dest`` is the worktree its git admin directory points back to.

        Returns the worktree's common git directory (its repo cache).

        A recreated cache can leave an old worktree pointing at an admin
        directory that now belongs to another worktree with the same name;
        reading it would report that other worktree's state.
        """
        out = self._in(dest, ["rev-parse", "--absolute-git-dir"], capture=True).text
        admin = Path(out.strip())
        try:
            back = (admin / (admin / "gitdir").read_text().strip()).resolve()
        except OSError:
            back = None
        if back != (dest / ".git").resolve():
            raise GitError(
                f"{dest} is not registered as a worktree of its repo cache",
                category="failed",
                hint=(
                    "if the workspace directory moved, run git worktree repair in the repo; "
                    "otherwise archive with --force after checking it"
                ),
            )
        try:
            return (admin / (admin / "commondir").read_text().strip()).resolve()
        except OSError:
            return admin

    # -- plumbing ----------------------------------------------------------

    def _cache(self, url: str) -> RepoCache:
        return RepoCache(
            cache_path(url, root=self._cache_dir),
            error=GitError,
            map_error=_git_error,
            git=self._git,
            timeout=self._timeout,
            slow_timeout=self._slow_timeout,
            lock_timeout=self._lock_timeout,
        )

    def _in(
        self, worktree: Path, args: Sequence[str], *, capture: bool = False, check: bool = True
    ) -> GitResult:
        """Run ``git <args>`` in ``worktree`` (no auth: these commands never fetch)."""
        try:
            return run_git(
                args,
                cwd=worktree,
                git=self._git,
                timeout=self._timeout,
                capture=capture,
                check=check,
                ceiling=True,
            )
        except GitCommandError as exc:
            raise _git_error(exc) from exc

    def _has_ref(self, wt: Path, ref: str) -> bool:
        args = ["show-ref", "--verify", "--quiet", ref]
        return self._in(wt, args, check=False).returncode == 0

    def _is_ancestor(self, cache: RepoCache, ancestor: str, descendant: str) -> bool:
        args = ["merge-base", "--is-ancestor", ancestor, descendant]
        return cache.run(args, check=False).returncode == 0


def _delete_tree(path: Path) -> None:
    """Delete ``path`` (gone already is fine); failures are attributed locally."""
    if not path.exists():
        return
    try:
        shutil.rmtree(path)
    except OSError as exc:
        raise WorkspaceError(
            f"could not delete {path}: {exc.strerror or exc}",
            category="failed",
            system="local",
            hint="check its permissions and that no process holds it, then retry",
        ) from exc


def _git_error(exc: GitCommandError) -> GitError:
    """Map a failed git run; a branch or directory already in use is a conflict."""
    stderr = exc.stderr.lower()
    if any(marker in stderr for marker in _BRANCH_IN_USE):
        return GitError(
            str(exc), returncode=exc.returncode, category="conflict", hint=_BRANCH_IN_USE_HINT
        )
    if "already exists" in stderr:
        return GitError(str(exc), returncode=exc.returncode, category="conflict", hint=_EXISTS_HINT)
    return GitError(str(exc), returncode=exc.returncode, **attribution(exc))


def _parse_status(out: str) -> tuple[str | None, str | None, int, int, int, int]:
    """Parse ``git status --porcelain=v2 --branch`` output.

    Returns ``(branch, upstream, ahead, behind, modified, untracked)``.
    """
    branch: str | None = None
    upstream: str | None = None
    ahead = 0
    behind = 0
    modified = 0
    untracked = 0
    for line in out.splitlines():
        if line.startswith("# branch.head "):
            head = line[len("# branch.head ") :].strip()
            branch = None if head == "(detached)" else head
        elif line.startswith("# branch.upstream "):
            upstream = line[len("# branch.upstream ") :].strip() or None
        elif line.startswith("# branch.ab "):
            parts = line[len("# branch.ab ") :].split()
            for p in parts:
                if p.startswith("+"):
                    ahead = int(p[1:])
                elif p.startswith("-"):
                    behind = int(p[1:])
        elif line.startswith("?"):
            untracked += 1
        elif line.startswith(("1 ", "2 ", "u ")):
            modified += 1
    return branch, upstream, ahead, behind, modified, untracked
