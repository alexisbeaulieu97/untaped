"""``LocalGitRepos``: plain clones of subscribed repos, driven through ``run_git``.

Each managed repo is an ordinary clone under ``dotfiles.repos_dir`` (not a
bare cache: ``link`` targets need a working tree, and the user may edit it).
A fast-forward is the only way the tool ever moves a working tree, and only
when it is clean; nothing here rewrites history or touches a registered
checkout's branch.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from untaped.sdk import GitCommandError, attribution, run_git
from untaped_dotfiles.errors import GitError
from untaped_dotfiles.infrastructure.trees import GitRefTree, WorkingTree


class LocalGitRepos:
    """:class:`GitRepos` over real clones."""

    def __init__(self, *, git: str = "git", timeout: float = 60.0, slow_timeout: float = 600.0):
        self._git = git
        self._timeout = timeout
        self._slow_timeout = slow_timeout

    def _run(
        self,
        path: Path | None,
        args: list[str],
        *,
        timeout: float | None = None,
        retry: bool = False,
        what: str | None = None,
    ) -> str:
        try:
            result = run_git(
                args,
                cwd=path,
                git=self._git,
                timeout=timeout or self._timeout,
                capture=True,
                retry_transient=retry,
            )
        except GitCommandError as exc:
            where = f" in {path}" if path else ""
            raise GitError(
                f"{what or f'git {args[0]}'} failed{where}: {exc}", **attribution(exc)
            ) from exc
        return result.text

    def clone(self, url: str, dest: Path, *, ref: str | None) -> str:
        dest.parent.mkdir(parents=True, exist_ok=True)
        args = ["clone", "--quiet", *(["--branch", ref] if ref else []), "--", url, str(dest)]
        self._run(None, args, timeout=self._slow_timeout, retry=True, what=f"clone of {url}")
        return self._run(dest, ["symbolic-ref", "--short", "HEAD"]).strip()

    def fetch(self, path: Path) -> None:
        self._run(
            path, ["fetch", "--quiet", "--prune", "origin"], timeout=self._slow_timeout, retry=True
        )

    def head(self, path: Path) -> str:
        return self._run(path, ["rev-parse", "HEAD"]).strip()

    def is_clean(self, path: Path) -> bool:
        return not self._run(path, ["status", "--porcelain", "--untracked-files=normal"]).strip()

    def behind(self, path: Path, ref: str) -> int:
        out = self._run(path, ["rev-list", "--count", f"HEAD..origin/{ref}"]).strip()
        return int(out or 0)

    def changed_paths(self, path: Path, ref: str, sources: Sequence[str]) -> set[str]:
        if not sources:
            return set()
        out = self._run(
            path, ["diff", "--name-only", "-z", "HEAD", f"origin/{ref}", "--", *sources]
        )
        return {name for name in out.split("\0") if name}

    def fast_forward(self, path: Path, ref: str) -> bool:
        before = self.head(path)
        self._run(path, ["merge", "--ff-only", "--quiet", f"origin/{ref}"], what="fast-forward")
        return self.head(path) != before

    def ref_tree(self, path: Path, ref: str) -> GitRefTree:
        return GitRefTree(path, f"origin/{ref}", git=self._git, timeout=self._timeout)

    def working_tree(self, path: Path) -> WorkingTree:
        return WorkingTree(path)
