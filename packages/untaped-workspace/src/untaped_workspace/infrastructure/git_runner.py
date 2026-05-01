"""Subprocess wrapper around ``git``.

Domain layers depend on a ``GitRunner`` Protocol; this is the concrete
adapter. Every call shells out to the system ``git`` binary; failures are
mapped to :class:`GitError`.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from untaped_workspace.domain import RepoStatus
from untaped_workspace.errors import GitError
from untaped_workspace.infrastructure.bare_cache import cache_path_for


class GitRunner:
    def __init__(self, *, git: str = "git") -> None:
        self._git = git
        # Resolve the binary once. We don't fail here on missing git so the
        # error surfaces at first call (and so tests can construct a runner
        # without git on PATH).
        self._git_path = shutil.which(git)

    # cache --------------------------------------------------------------

    def ensure_bare(self, url: str, *, cache_dir: Path | None = None) -> Path:
        """Ensure a bare clone of ``url`` exists in the cache; return its path."""
        bare = cache_path_for(url, cache_dir=cache_dir)
        if bare.is_dir() and (bare / "HEAD").is_file():
            return bare
        bare.parent.mkdir(parents=True, exist_ok=True)
        self._run(["clone", "--bare", url, str(bare)])
        return bare

    def bare_fetch(self, bare_path: Path) -> None:
        self._run(["fetch", "--all", "--prune"], cwd=bare_path)

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
        cmd = ["clone", "--reference", str(bare)]
        if branch is not None:
            cmd += ["--branch", branch]
        cmd += [url, str(dest)]
        self._run(cmd)

    # status -------------------------------------------------------------

    def status(self, repo_path: Path) -> RepoStatus:
        out = self._run(["status", "--porcelain=v2", "--branch"], cwd=repo_path, capture=True)
        return _parse_status(out)

    def is_dirty(self, repo_path: Path) -> bool:
        return self.status(repo_path).dirty

    # update -------------------------------------------------------------

    def fetch(self, repo_path: Path) -> None:
        self._run(["fetch", "--all", "--prune"], cwd=repo_path)

    def ff_only_pull(self, repo_path: Path, *, branch: str) -> None:
        self._run(["merge", "--ff-only", f"origin/{branch}"], cwd=repo_path)

    def default_branch(self, bare_path: Path) -> str | None:
        """Return the branch the bare's HEAD points at, or ``None``."""
        try:
            out = self._run(["symbolic-ref", "--short", "HEAD"], cwd=bare_path, capture=True)
        except GitError:
            return None
        return out.strip() or None

    # internal -----------------------------------------------------------

    def _run(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        capture: bool = False,
    ) -> str:
        if self._git_path is None:
            raise GitError(f"`{self._git}` not found on PATH")
        result = subprocess.run(
            [self._git_path, *args],
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            raise GitError(
                f"git {' '.join(args)} failed: {stderr or 'no stderr'}",
                returncode=result.returncode,
            )
        return result.stdout if capture else ""


def _parse_status(out: str) -> RepoStatus:
    """Parse ``git status --porcelain=v2 --branch`` output."""
    branch: str | None = None
    ahead = 0
    behind = 0
    modified = 0
    untracked = 0
    for line in out.splitlines():
        if line.startswith("# branch.head "):
            head = line[len("# branch.head ") :].strip()
            branch = None if head == "(detached)" else head
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
        ahead=ahead,
        behind=behind,
        modified=modified,
        untracked=untracked,
    )
