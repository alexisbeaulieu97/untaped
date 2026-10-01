"""Git worktrees over a shared bare cache: one cache per repo URL, many worktrees.

Each repo URL maps to a bare cache (``cache_path_for``) whose
``origin`` remote fetches into ``refs/remotes/origin/*``; workspaces get
``git worktree`` checkouts from it. Every cache write runs under the cache's
advisory lock, so parallel provisioning of one repo serializes safely. All
git runs go through :func:`untaped.capability_api.run_git`; failures become
:class:`GitError` keeping git's attribution, and a branch or directory that
is already in use becomes a ``conflict``.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from untaped.capabilities.workspace.domain.models import Checkout, WorktreeStatus
from untaped.capabilities.workspace.errors import GitError
from untaped.capabilities.workspace.infrastructure.bare_cache import cache_path_for
from untaped.capability_api import GitCommandError, attribution, file_lock, run_git

_FETCH_REFSPEC = "+refs/heads/*:refs/remotes/origin/*"
_BRANCH_IN_USE = ("already checked out", "is already used by worktree")
_BRANCH_IN_USE_HINT = (
    "the branch is checked out in another workspace: archive that workspace or pick another branch"
)
_EXISTS_HINT = "remove the existing directory or branch, or pick another name"


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
        with self._locked(cache):
            self._ensure_cache(cache, url)
            self._fetch(cache)
            self._run(["remote", "set-head", "origin", "--auto"], cwd=cache, check=False)
            picked = self._pick_base(cache, url, base)
            if branch is None:
                self._run(["worktree", "add", "--detach", target, f"origin/{picked}"], cwd=cache)
                return Checkout(
                    action="checked_out", base=picked, detail=f"read-only at origin/{picked}"
                )
            checkout = self._add_writable(cache, target, branch, picked)
            self._set_upstream(cache, branch)
            return checkout

    def status(self, dest: Path, *, branch: str | None, base: str) -> WorktreeStatus | None:
        """Git state of the worktree at ``dest``; ``None`` when it is missing."""
        if not dest.exists():
            return None
        out = self._run(["status", "--porcelain=v2", "--branch"], cwd=dest, capture=True)
        head, upstream, ahead, behind, modified, untracked = _parse_status(out)
        return WorktreeStatus(
            branch=head,
            upstream=upstream,
            ahead=ahead,
            behind=behind,
            modified=modified,
            untracked=untracked,
            stashed=self._stashed(dest, branch),
            unpushed=self._unpushed(dest, branch, base),
        )

    def fetch(self, url: str) -> None:
        """Fetch the cache for ``url`` (no-op when it is missing)."""
        cache = self._cache(url)
        if not _cache_ready(cache):
            return
        with self._locked(cache):
            self._fetch(cache)

    def cache_exists(self, url: str) -> bool:
        return _cache_ready(self._cache(url))

    def remove(self, url: str, dest: Path, *, force: bool) -> None:
        """Remove the worktree at ``dest`` and prune stale worktree entries."""
        cache = self._cache(url)
        if not _cache_ready(cache):
            if not force:
                raise GitError(
                    f"the repo cache for {url} is missing",
                    category="failed",
                    hint="pass --force to delete the directory",
                )
            if dest.exists():
                shutil.rmtree(dest)
            return
        with self._locked(cache):
            if dest.exists():
                flags = ["--force"] if force else []
                self._run(["worktree", "remove", *flags, str(dest.absolute())], cwd=cache)
            self._run(["worktree", "prune"], cwd=cache)

    # -- checkout steps ----------------------------------------------------

    def _ensure_cache(self, cache: Path, url: str) -> None:
        if not _cache_ready(cache):
            self._run(["init", "--bare", "--quiet", str(cache)], cwd=cache.parent)
            self._run(["remote", "add", "origin", url], cwd=cache)
        self._run(["config", "--replace-all", "remote.origin.fetch", _FETCH_REFSPEC], cwd=cache)

    def _fetch(self, cache: Path) -> None:
        self._run(
            ["fetch", "--prune", "--quiet", "origin"],
            cwd=cache,
            timeout=self._slow_timeout,
            retry_transient=True,
        )

    def _pick_base(self, cache: Path, url: str, base: str | None) -> str:
        if base is None:
            head = self._run(
                ["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"],
                cwd=cache,
                capture=True,
                check=False,
            ).strip()
            base = head.removeprefix("origin/") if head else "main"
        if not self._has_ref(cache, f"refs/remotes/origin/{base}"):
            raise GitError(
                f"base branch '{base}' not found on origin of {url}", category="not_found"
            )
        return base

    def _add_writable(self, cache: Path, target: str, branch: str, base: str) -> Checkout:
        local = self._has_ref(cache, f"refs/heads/{branch}")
        remote = self._has_ref(cache, f"refs/remotes/origin/{branch}")
        tracking = f"origin/{branch}"
        if local and remote:
            detail = self._reconcile(cache, branch)
            self._run(["worktree", "add", target, branch], cwd=cache)
            return Checkout(action="checked_out", base=base, detail=detail)
        if remote:
            self._run(["worktree", "add", "-b", branch, target, tracking], cwd=cache)
            return Checkout(action="checked_out", base=base, detail=f"tracking {tracking}")
        if local:
            self._run(["worktree", "add", target, branch], cwd=cache)
            return Checkout(action="checked_out", base=base, detail="resumed local branch")
        self._run(["worktree", "add", "-b", branch, target, f"origin/{base}"], cwd=cache)
        return Checkout(action="created", base=base, detail=f"from origin/{base}")

    def _reconcile(self, cache: Path, branch: str) -> str:
        """Fast-forward a local branch behind its remote; describe how the two relate."""
        tracking = f"origin/{branch}"
        if self._is_ancestor(cache, branch, tracking):
            self._run(["branch", "-f", branch, tracking], cwd=cache)
            return f"tracking {tracking}"
        if self._is_ancestor(cache, tracking, branch):
            return f"resumed; ahead of {tracking}"
        return f"resumed; diverged from {tracking}"

    def _set_upstream(self, cache: Path, branch: str) -> None:
        self._run(["config", f"branch.{branch}.remote", "origin"], cwd=cache)
        self._run(["config", f"branch.{branch}.merge", f"refs/heads/{branch}"], cwd=cache)

    # -- status helpers ----------------------------------------------------

    def _stashed(self, dest: Path, branch: str | None) -> int:
        label = branch if branch is not None else "(no branch)"
        out = self._run(["stash", "list", "--format=%gs"], cwd=dest, capture=True)
        prefixes = (f"WIP on {label}:", f"On {label}:")
        return sum(1 for line in out.splitlines() if line.startswith(prefixes))

    def _unpushed(self, dest: Path, branch: str | None, base: str) -> int:
        if branch is None:
            return 0
        remote = f"refs/remotes/origin/{branch}"
        upstream = f"origin/{branch}" if self._has_ref(dest, remote) else f"origin/{base}"
        out = self._run(["rev-list", "--count", f"{upstream}..HEAD"], cwd=dest, capture=True)
        return int(out.strip() or 0)

    # -- plumbing ----------------------------------------------------------

    def _cache(self, url: str) -> Path:
        return cache_path_for(url, cache_dir=self._cache_dir)

    @contextmanager
    def _locked(self, cache: Path) -> Iterator[None]:
        cache.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(
            Path(f"{cache}.lock"),
            timeout=self._lock_timeout,
            error=GitError,
            busy=f"repo cache is busy (another untaped process): {cache}",
            failed=f"could not lock repo cache {cache}",
        ):
            yield

    def _has_ref(self, cwd: Path, ref: str) -> bool:
        return self._returncode(["show-ref", "--verify", "--quiet", ref], cwd=cwd) == 0

    def _is_ancestor(self, cwd: Path, ancestor: str, descendant: str) -> bool:
        args = ["merge-base", "--is-ancestor", ancestor, descendant]
        return self._returncode(args, cwd=cwd) == 0

    def _returncode(self, args: Sequence[str], *, cwd: Path) -> int:
        try:
            result = run_git(
                args, cwd=cwd, git=self._git, timeout=self._timeout, check=False, ceiling=True
            )
        except GitCommandError as exc:
            raise _git_error(exc) from exc
        return result.returncode

    def _run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        capture: bool = False,
        check: bool = True,
        timeout: float | None = None,
        retry_transient: bool = False,
    ) -> str:
        try:
            result = run_git(
                args,
                cwd=cwd,
                git=self._git,
                timeout=self._timeout if timeout is None else timeout,
                capture=capture,
                check=check,
                ceiling=True,
                retry_transient=retry_transient,
            )
        except GitCommandError as exc:
            raise _git_error(exc) from exc
        if result.returncode != 0:
            return ""
        return result.text if capture else ""


def _cache_ready(cache: Path) -> bool:
    return (cache / "HEAD").exists()


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
