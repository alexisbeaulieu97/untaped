"""Source trees: the files of a repo read from a fetched ref (git) or from a working tree (fs).

A ``copy`` or ``merge`` file in a managed clone reads ``origin/<ref>``, so it
follows its own policy even while the working tree is held back for a
``manual`` link file; a registered checkout reads its working tree, because
its owner edits there and decides what it contains. Both refuse a path that
escapes the repo.
"""

from __future__ import annotations

import os
from pathlib import Path

from untaped.sdk import GitCommandError, attribution, run_git
from untaped_dotfiles.domain.models import TreeFile
from untaped_dotfiles.errors import DotfilesError, GitError


class GitRefTree:
    """The files of ``ref`` in the repo at ``path``, read with ``git ls-tree`` and ``git show``."""

    def __init__(self, path: Path, ref: str, *, git: str = "git", timeout: float = 60.0) -> None:
        self._path = path
        self._ref = ref
        self._git = git
        self._timeout = timeout
        self._commit: str | None = None

    @property
    def commit(self) -> str | None:
        if self._commit is None:
            self._commit = self._run(["rev-parse", "--verify", f"{self._ref}^{{commit}}"]).strip()
        return self._commit

    def _run(self, args: list[str], *, check: bool = True) -> str:
        try:
            result = run_git(
                args,
                cwd=self._path,
                git=self._git,
                timeout=self._timeout,
                capture=True,
                check=check,
            )
        except GitCommandError as exc:
            raise GitError(
                f"git {args[0]} failed in {self._path}: {exc}", **attribution(exc)
            ) from exc
        return result.text

    def _type(self, path: str) -> str | None:
        try:
            result = run_git(
                ["cat-file", "-t", f"{self._ref}:{path}"],
                cwd=self._path,
                git=self._git,
                timeout=self._timeout,
                capture=True,
                check=False,
            )
        except GitCommandError as exc:
            raise GitError(
                f"git cat-file failed in {self._path}: {exc}", **attribution(exc)
            ) from exc
        return result.text.strip() if result.returncode == 0 else None

    def exists(self, path: str) -> bool:
        return self._type(path) in ("blob", "tree")

    def is_dir(self, path: str) -> bool:
        return self._type(path) == "tree"

    def files(self, path: str) -> list[TreeFile]:
        out = self._run(["ls-tree", "-r", "-z", self._ref, "--", path])
        found: list[TreeFile] = []
        for entry in out.split("\0"):
            if not entry:
                continue
            meta, _, name = entry.partition("\t")
            mode, kind, _sha = meta.split()
            if kind == "blob":
                found.append(TreeFile(path=name, executable=mode == "100755"))
        return sorted(found, key=lambda f: f.path)

    def read(self, path: str) -> bytes:
        try:
            result = run_git(
                ["show", f"{self._ref}:{path}"],
                cwd=self._path,
                git=self._git,
                timeout=self._timeout,
                capture=True,
            )
        except GitCommandError as exc:
            raise GitError(
                f"could not read {path} at {self._ref} in {self._path}: {exc}", **attribution(exc)
            ) from exc
        return result.stdout


class WorkingTree:
    """The files of a checkout's working tree (``.git`` left out)."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    @property
    def commit(self) -> str | None:
        return None

    def resolve(self, path: str) -> Path:
        """The absolute path of ``path``; refuses one that leaves the repo (symlinks included)."""
        candidate = (self._root / path).resolve()
        if candidate != self._root and self._root not in candidate.parents:
            raise DotfilesError(f"source {path!r} leaves the repo at {self._root}")
        return candidate

    def exists(self, path: str) -> bool:
        return self.resolve(path).exists()

    def is_dir(self, path: str) -> bool:
        return self.resolve(path).is_dir()

    def files(self, path: str) -> list[TreeFile]:
        root = self.resolve(path)
        found: list[TreeFile] = []
        for directory, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(name for name in dirnames if name != ".git")
            for filename in filenames:
                full = Path(directory) / filename
                relative = full.relative_to(self._root).as_posix()
                found.append(TreeFile(path=relative, executable=os.access(full, os.X_OK)))
        return sorted(found, key=lambda f: f.path)

    def read(self, path: str) -> bytes:
        return self.resolve(path).read_bytes()
