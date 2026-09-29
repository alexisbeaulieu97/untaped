"""Read files as they are at one commit of the working directory's repository.

``--source-ref REF`` resolves ``REF`` to a commit once, then reads each named
file or directory at that commit (``git ls-tree`` and ``git show SHA:PATH``),
never from the working tree, so local edits cannot leak into what is applied
or tested. ``HEAD`` must be pushed, as for ``--scm-branch HEAD``, because the
controller checks out the remote's copy. Paths are relative to the working
directory, as typed on the command line.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from untaped.capabilities.awx.infrastructure.git_head import pushed_branch
from untaped.capability_api import ConfigError, GitCommandError, GitResult, git_toplevel, run_git

_TIMEOUT = 30.0
DOCUMENT_SUFFIXES = (".yml", ".yaml")


@dataclass(frozen=True)
class GitSource:
    """One repository pinned at one commit; ``ref`` is how the user named it."""

    ref: str
    sha: str
    root: Path
    cwd: Path

    @classmethod
    def resolve(cls, ref: str, *, cwd: Path | None = None) -> GitSource:
        """Pin ``ref`` (a branch, tag, commit or pushed ``HEAD``) to its commit."""
        cwd = (cwd or Path.cwd()).resolve()
        try:
            root = git_toplevel(cwd)
        except GitCommandError as exc:
            raise ConfigError(f"--source-ref: {exc}") from exc
        if root is None:
            raise ConfigError(f"--source-ref: {cwd} is not inside a git repository")
        if ref == "HEAD":
            pushed_branch(cwd, flag="--source-ref")
        unknown = ConfigError(f"--source-ref {ref}: not a commit in {root}")
        if ref.startswith("-"):
            raise unknown
        source = cls(ref=ref, sha="", root=root, cwd=cwd)
        result = source._git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False)
        if result.returncode != 0:
            raise unknown
        return cls(ref=ref, sha=result.text.strip(), root=root, cwd=cwd)

    def files(self, path: Path, *, suffixes: tuple[str, ...] = DOCUMENT_SUFFIXES) -> list[str]:
        """The file ``path`` names, or the ``suffixes`` files under it, repo-relative."""
        rel = self.repo_path(path)
        shown = rel or "."
        kind = self._git("cat-file", "-t", f"{self.sha}:{rel}", check=False)
        if kind.returncode != 0:
            raise ConfigError(f"{shown} does not exist at {self.ref}")
        if kind.text.strip() == "blob":
            return [rel]
        listing = self._git("ls-tree", "-r", "-z", self.sha, "--", shown).text
        found = sorted(
            name
            for meta, _, name in (entry.partition("\t") for entry in listing.split("\0") if entry)
            if meta.split()[1] == "blob" and name.endswith(suffixes)
        )
        if not found:
            raise ConfigError(f"no {'/'.join(suffixes)} files under {shown} at {self.ref}")
        return found

    def read_text(self, rel: str) -> str:
        """The UTF-8 text of the repo-relative file ``rel`` at the pinned commit."""
        data = self._git("show", f"{self.sha}:{rel}").stdout
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ConfigError(f"cannot read {self.label(rel)}: not UTF-8 text") from exc

    def label(self, rel: str) -> str:
        """How messages name a file: ``REF:PATH``, git's own spelling."""
        return f"{self.ref}:{rel}"

    def repo_path(self, path: Path) -> str:
        """``path`` (relative to the working directory) relative to the repository root."""
        absolute = Path(os.path.normpath(self.cwd / path))
        for candidate in (absolute, absolute.resolve()):
            if candidate.is_relative_to(self.root):
                rel = candidate.relative_to(self.root).as_posix()
                return "" if rel == "." else rel
        raise ConfigError(f"{path} is outside the repository {self.root}")

    def _git(self, *args: str, check: bool = True) -> GitResult:
        try:
            return run_git(list(args), cwd=self.root, timeout=_TIMEOUT, capture=True, check=check)
        except GitCommandError as exc:
            raise ConfigError(f"--source-ref {self.ref}: {exc}") from exc
