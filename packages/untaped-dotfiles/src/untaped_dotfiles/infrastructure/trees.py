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

from untaped.sdk import GitCommandError, GitResult, attribution, run_git
from untaped_dotfiles.domain.models import TreeFile
from untaped_dotfiles.errors import DotfilesError, GitError


class GitRefTree:
    """The files of ``ref`` in the repo at ``path``.

    One ``git ls-tree`` lists the whole tree on first use; ``git show`` reads
    a file.
    """

    def __init__(self, path: Path, ref: str, *, git: str = "git", timeout: float = 60.0) -> None:
        self._path = path
        self._ref = ref
        self._git = git
        self._timeout = timeout
        self._commit: str | None = None
        self._entries: dict[str, TreeFile | None] | None = None
        """Every path in the tree: a :class:`TreeFile` for a blob, ``None`` for a directory."""

    @property
    def commit(self) -> str | None:
        if self._commit is None:
            self._commit = self._run(
                ["rev-parse", "--verify", f"{self._ref}^{{commit}}"]
            ).text.strip()
        return self._commit

    def _run(self, args: list[str], *, what: str | None = None) -> GitResult:
        try:
            return run_git(args, cwd=self._path, git=self._git, timeout=self._timeout, capture=True)
        except GitCommandError as exc:
            raise GitError(
                f"{what or f'git {args[0]}'} failed in {self._path}: {exc}", **attribution(exc)
            ) from exc

    def _tree(self) -> dict[str, TreeFile | None]:
        if self._entries is None:
            out = self._run(["ls-tree", "-r", "-t", "-z", self._ref]).text
            entries: dict[str, TreeFile | None] = {}
            for entry in out.split("\0"):
                if not entry:
                    continue
                meta, _, name = entry.partition("\t")
                mode, kind, _sha = meta.split()
                if kind == "blob":
                    entries[name] = TreeFile(path=name, executable=mode == "100755")
                elif kind == "tree":
                    entries[name] = None
            self._entries = entries
        return self._entries

    def exists(self, path: str) -> bool:
        return path in self._tree()

    def is_dir(self, path: str) -> bool:
        entries = self._tree()
        return path in entries and entries[path] is None

    def files(self, path: str) -> list[TreeFile]:
        prefix = path.rstrip("/") + "/"
        return sorted(
            (entry for name, entry in self._tree().items() if entry and name.startswith(prefix)),
            key=lambda f: f.path,
        )

    def read(self, path: str) -> bytes:
        return self._run(
            ["show", f"{self._ref}:{path}"], what=f"reading {path} at {self._ref}"
        ).stdout


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
        try:
            return self.resolve(path).read_bytes()
        except OSError as exc:
            raise DotfilesError(f"could not read source {path}: {exc.strerror or exc}") from exc
