"""Subprocess wrapper around ``git``.

Domain layers depend on a ``GitRunner`` Protocol; this is the concrete
adapter. Every call shells out to the system ``git`` binary; failures are
mapped to :class:`GitError`.

Invocations do not wait for interactive credential prompts (stdin closed,
terminal and credential-manager prompts disabled, ssh in ``BatchMode``
unless the user set ``GIT_SSH_COMMAND``/``GIT_SSH``) so a missing
credential normally fails fast instead of hanging a sweep, and per-repo
calls set ``GIT_CEILING_DIRECTORIES`` so git never falls through to a
repository enclosing the target directory.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from untaped.capabilities.workspace.domain import BareCacheEntry, RepoStatus
from untaped.capabilities.workspace.domain.prune_safety import (
    DIRTY_WORKTREE_BLOCKER,
    STASH_BLOCKER,
    UNREACHABLE_COMMITS_BLOCKER,
)
from untaped.capabilities.workspace.errors import GitError
from untaped.capabilities.workspace.infrastructure.bare_cache import cache_path_for

DEFAULT_TIMEOUT = 60.0
"""Per-call timeout (seconds) for fast/local git ops (status, config, …)."""

DEFAULT_SLOW_TIMEOUT = 600.0
"""Per-call timeout (seconds) for network ops (clone, fetch)."""


class GitRunner:
    def __init__(
        self,
        *,
        git: str = "git",
        timeout: float = DEFAULT_TIMEOUT,
        slow_timeout: float = DEFAULT_SLOW_TIMEOUT,
    ) -> None:
        self._git = git
        # Resolve the binary once. We don't fail here on missing git so the
        # error surfaces at first call (and so tests can construct a runner
        # without git on PATH).
        self._git_path = shutil.which(git)
        self._timeout = timeout
        self._slow_timeout = slow_timeout

    # cache --------------------------------------------------------------

    def bare_cache_path(self, url: str, *, cache_dir: Path) -> Path:
        return cache_path_for(url, cache_dir=cache_dir)

    def ensure_bare(self, url: str, *, cache_dir: Path) -> BareCacheEntry:
        """Ensure a bare clone of ``url`` exists in the cache."""
        bare = self.bare_cache_path(url, cache_dir=cache_dir)
        if bare.is_dir() and (bare / "HEAD").is_file():
            return BareCacheEntry(path=bare, created=False)
        # ``mkdir(parents=True, exist_ok=True)`` is the thread-safety
        # boundary for repo-level sync parallelism: different URLs can
        # race on the same ``cache_dir`` here, and ``exist_ok=True`` (plus the
        # per-level ``FileExistsError`` handling in ``os.makedirs``)
        # makes it idempotent. Don't replace with a non-idempotent
        # variant.
        bare.parent.mkdir(parents=True, exist_ok=True)
        self._clone(["clone", "--bare", url, str(bare)], dest=bare)
        self._protect_cache_objects(bare)
        return BareCacheEntry(path=bare, created=True)

    def bare_fetch(self, bare_path: Path) -> None:
        # ``clone --bare`` configures no fetch refspec, so a bare
        # ``fetch --all`` would only update FETCH_HEAD. Mirror branches
        # explicitly (also repairs caches created before this refspec).
        self._run(
            ["fetch", "--prune", "origin", "+refs/heads/*:refs/heads/*"],
            cwd=bare_path,
            timeout=self._slow_timeout,
        )
        self._protect_cache_objects(bare_path)

    def _protect_cache_objects(self, bare_path: Path) -> None:
        """Never auto-gc or prune the cache's objects.

        Clones made before ``--dissociate`` was used still borrow objects
        through ``objects/info/alternates``; pruning objects that became
        unreachable in the cache (deleted or force-pushed branches) would
        corrupt them.
        """
        self._run(["config", "gc.pruneExpire", "never"], cwd=bare_path)
        self._run(["config", "gc.auto", "0"], cwd=bare_path)

    # workspace clone ----------------------------------------------------

    def clone_with_reference(
        self,
        *,
        url: str,
        dest: Path,
        bare: Path,
        branch: str | None = None,
    ) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        # ``--dissociate`` copies the borrowed objects into the clone, so the
        # cache stays a pure accelerator: pruning it never breaks clones.
        cmd = ["clone", "--reference", str(bare), "--dissociate"]
        if branch is not None:
            cmd += ["--branch", branch]
        cmd += [url, str(dest)]
        self._clone(cmd, dest=dest)

    def _clone(self, cmd: list[str], *, dest: Path) -> None:
        """Run a clone; remove ``dest`` on failure if this call created it.

        A clone killed by the timeout leaves a partial directory that
        later syncs would treat as an existing (dirty or broken) clone.
        """
        existed = dest.exists()
        try:
            self._run(cmd, timeout=self._slow_timeout)
        except GitError:
            if not existed:
                shutil.rmtree(dest, ignore_errors=True)
            raise

    # status -------------------------------------------------------------

    def status(self, repo_path: Path) -> RepoStatus:
        out = self._run(["status", "--porcelain=v2", "--branch"], cwd=repo_path, capture=True)
        return _parse_status(out)

    def prune_blockers(self, repo_path: Path) -> tuple[str, ...]:
        blockers: list[str] = []
        if self.status(repo_path).dirty:
            blockers.append(DIRTY_WORKTREE_BLOCKER)

        stash = self._run(["stash", "list", "--format=%H"], cwd=repo_path, capture=True)
        if stash.strip():
            blockers.append(STASH_BLOCKER)

        if self._ref_commit_exists(repo_path, "HEAD"):
            out = self._run(
                ["rev-list", "HEAD", "--branches", "--tags", "--not", "--remotes", "--count"],
                cwd=repo_path,
                capture=True,
            )
            if int(out.strip() or "0") > 0:
                blockers.append(UNREACHABLE_COMMITS_BLOCKER)

        return tuple(blockers)

    # update -------------------------------------------------------------

    def fetch(self, repo_path: Path) -> None:
        self._run(
            ["fetch", "--prune", "origin", "+refs/heads/*:refs/remotes/origin/*"],
            cwd=repo_path,
            timeout=self._slow_timeout,
        )

    def ff_only_pull(self, repo_path: Path, *, branch: str) -> None:
        # Merge the branch's configured upstream, not ``origin/<local name>``:
        # a local branch may track a differently named remote branch.
        del branch
        self._run(["merge", "--ff-only", "@{upstream}"], cwd=repo_path)

    def has_branch(self, repo_path: Path, *, branch: str) -> bool:
        """Return whether ``branch`` exists locally or as ``origin/<branch>``."""
        return self._ref_exists(repo_path, f"refs/heads/{branch}") or self._ref_commit_exists(
            repo_path, f"refs/remotes/origin/{branch}"
        )

    def checkout_branch(self, repo_path: Path, *, branch: str) -> None:
        if self._ref_exists(repo_path, f"refs/heads/{branch}"):
            self._run(["checkout", branch], cwd=repo_path)
            return
        remote_ref = f"refs/remotes/origin/{branch}"
        if self._ref_commit_exists(repo_path, remote_ref):
            self._run(["checkout", "-b", branch, remote_ref], cwd=repo_path)
            self._run(["config", f"branch.{branch}.remote", "origin"], cwd=repo_path)
            self._run(["config", f"branch.{branch}.merge", f"refs/heads/{branch}"], cwd=repo_path)
            return
        self._run(["checkout", "-b", branch], cwd=repo_path)

    def default_branch(self, bare_path: Path) -> str | None:
        """Return the branch the bare's HEAD points at, or ``None``."""
        try:
            out = self._run(["symbolic-ref", "--short", "HEAD"], cwd=bare_path, capture=True)
        except GitError:
            return None
        return out.strip() or None

    # introspection (used by `workspace adopt`) --------------------------

    def read_remote_url(self, repo_path: Path, *, remote: str = "origin") -> str | None:
        """Return the URL of ``remote`` in ``repo_path``, or ``None``."""
        try:
            out = self._run(
                ["config", "--get", f"remote.{remote}.url"],
                cwd=repo_path,
                capture=True,
            )
        except GitError:
            return None
        return out.strip() or None

    def read_current_branch(self, repo_path: Path) -> str | None:
        """Return the current branch name, or ``None`` for detached HEAD."""
        try:
            out = self._run(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_path, capture=True)
        except GitError:
            return None
        name = out.strip()
        if not name or name == "HEAD":
            return None
        return name

    # internal -----------------------------------------------------------

    def _ref_exists(self, repo_path: Path, ref: str) -> bool:
        try:
            self._run(["show-ref", "--verify", "--quiet", ref], cwd=repo_path)
        except GitError:
            return False
        return True

    def _ref_commit_exists(self, repo_path: Path, ref: str) -> bool:
        try:
            self._run(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], cwd=repo_path)
        except GitError:
            return False
        return True

    def _run(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        capture: bool = False,
        timeout: float | None = None,
    ) -> str:
        if self._git_path is None:
            raise GitError(f"`{self._git}` not found on PATH")
        effective_timeout = self._timeout if timeout is None else timeout
        # Name only the subcommand: full argv carries absolute paths and
        # refspecs that drown the useful part of the message.
        label = f"git {args[0]}" if args else "git"
        try:
            result = subprocess.run(
                [self._git_path, *args],
                cwd=cwd,
                env=_git_env(cwd),
                stdin=subprocess.DEVNULL,
                text=True,
                capture_output=True,
                check=False,
                timeout=effective_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise GitError(f"{label} timed out after {effective_timeout:g}s") from exc
        except OSError as exc:
            raise GitError(f"{label} could not run: {exc}") from exc
        if result.returncode != 0:
            raise GitError(
                f"{label} failed: {_stderr_gist(result.stderr or '')}",
                returncode=result.returncode,
            )
        return result.stdout if capture else ""


_GIST_LIMIT = 300


def _git_env(cwd: Path | None) -> dict[str, str]:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}
    if "GIT_SSH_COMMAND" not in env and "GIT_SSH" not in env:
        # Stop ssh from waiting on passphrase/host-key prompts. A user's own
        # GIT_SSH_COMMAND/GIT_SSH wins (note: this env var does take
        # precedence over a ``core.sshCommand`` setting).
        env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes"
    if cwd is not None:
        parent = str(Path(os.path.abspath(cwd)).parent)
        existing = env.get("GIT_CEILING_DIRECTORIES")
        env["GIT_CEILING_DIRECTORIES"] = os.pathsep.join(
            [parent, existing] if existing else [parent]
        )
    return env


def _stderr_gist(stderr: str) -> str:
    """Keep the ``fatal:``/``error:`` lines (or the last line), bounded."""
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    if not lines:
        return "no stderr"
    important = [line for line in lines if line.lower().startswith(("fatal:", "error:"))]
    gist = "; ".join(important or lines[-1:])
    if len(gist) > _GIST_LIMIT:
        gist = gist[: _GIST_LIMIT - 3] + "..."
    return gist


def _parse_status(out: str) -> RepoStatus:
    """Parse ``git status --porcelain=v2 --branch`` output."""
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
    return RepoStatus(
        branch=branch,
        upstream=upstream,
        ahead=ahead,
        behind=behind,
        modified=modified,
        untracked=untracked,
    )
