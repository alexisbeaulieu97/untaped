"""Read files as they are at one commit of the working directory's repository.

``--source-ref REF`` resolves ``REF`` to a commit once, then reads each named
file or directory at that commit (``git ls-tree`` and ``git show SHA:PATH``),
never from the working tree, so local edits cannot leak into what is applied
or tested. Symbolic links at the commit are refused rather than followed.
Paths are relative to the working directory, as typed on the command line.

``HEAD`` must always be pushed, as for ``--scm-branch HEAD``. Whatever makes
the controller check the commit out (a test run) also calls
:meth:`GitSource.require_pushed`, which accepts any ref whose commit a remote
has; ``apply`` only reads the files locally, so it does not.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from untaped.capabilities.awx.infrastructure.git_head import pushed_branch
from untaped.capability_api import (
    ConfigError,
    GitCommandError,
    GitResult,
    attribution,
    git_toplevel,
    run_git,
)

_TIMEOUT = 30.0
DOCUMENT_SUFFIXES = (".yml", ".yaml")
_SYMLINK_MODE = "120000"


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
            raise ConfigError(f"--source-ref: {exc}", **attribution(exc)) from exc
        if root is None:
            raise ConfigError(f"--source-ref: {cwd} is not inside a git repository", system="git")
        if ref == "HEAD":
            pushed_branch(cwd, flag="--source-ref")
        unknown = ConfigError(
            f"--source-ref {ref}: not a commit in {root}", category="not_found", system="git"
        )
        if ref.startswith("-"):
            raise unknown
        result = _git(
            root, ref, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False
        )
        if result.returncode != 0:
            raise unknown
        return cls(ref=ref, sha=result.text.strip(), root=root, cwd=cwd)

    def require_pushed(self) -> None:
        """Refuse a commit no remote has: the controller could not check it out.

        A remote branch or tag at the commit (``git ls-remote``) or a
        remote-tracking branch containing it (``git branch -r --contains``) will do.
        """
        remotes = self._git("remote").text.split()
        if not remotes:
            raise ConfigError(
                f"--source-ref {self.ref}: the repository has no remote", system="git"
            )
        for remote in remotes:
            listed = run_git(
                ["ls-remote", remote],
                cwd=self.root,
                timeout=_TIMEOUT,
                capture=True,
                check=False,
                retry_transient=True,
            )
            if listed.returncode == 0 and self.sha in listed.text.split():
                return
        if self._git("branch", "-r", "--contains", self.sha, check=False).text.strip():
            return
        raise ConfigError(
            f"--source-ref {self.ref}: commit {self.sha[:12]} is not on any remote; push it first",
            system="git",
        )

    def files(self, path: Path, *, suffixes: tuple[str, ...] = DOCUMENT_SUFFIXES) -> list[str]:
        """The file ``path`` names, or the ``suffixes`` files under it, repo-relative."""
        rel = self.repo_path(path)
        shown = rel or "."
        listing = self._git("ls-tree", "-r", "-z", self.sha, "--", shown).text
        entries = []
        for entry in filter(None, listing.split("\0")):
            meta, _, name = entry.partition("\t")
            mode, kind, _sha = meta.split()
            if kind == "blob" and (not rel or name == rel or name.startswith(f"{rel}/")):
                entries.append((mode, name))
        if not entries:
            raise ConfigError(
                f"{shown} does not exist at {self.ref}", category="not_found", system="git"
            )
        if entries[0][1] == rel:
            found = [entries[0]]
        else:
            found = sorted(entry for entry in entries if entry[1].endswith(suffixes))
            if not found:
                raise ConfigError(
                    f"no {'/'.join(suffixes)} files under {shown} at {self.ref}",
                    category="not_found",
                    system="git",
                )
        for mode, name in found:
            if mode == _SYMLINK_MODE:
                raise ConfigError(
                    f"{self.label(name)} is a symbolic link; --source-ref reads regular files only",
                    category="invalid",
                    system="git",
                )
        return [name for _mode, name in found]

    def read_text(self, rel: str) -> str:
        """The UTF-8 text of the repo-relative file ``rel`` at the pinned commit."""
        data = self._git("show", f"{self.sha}:{rel}").stdout
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ConfigError(
                f"cannot read {self.label(rel)}: not UTF-8 text", category="invalid", system="git"
            ) from exc

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
        raise ConfigError(
            f"{path} is outside the repository {self.root}", category="invalid", system="git"
        )

    def _git(self, *args: str, check: bool = True) -> GitResult:
        return _git(self.root, self.ref, *args, check=check)


def _git(root: Path, ref: str, *args: str, check: bool = True) -> GitResult:
    try:
        return run_git(list(args), cwd=root, timeout=_TIMEOUT, capture=True, check=check)
    except GitCommandError as exc:
        raise ConfigError(f"--source-ref {ref}: {exc}", **attribution(exc)) from exc
