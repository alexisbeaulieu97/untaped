"""Source trees: a source's files read from a store commit (a git source) or a folder.

A git source's packages, ``copy`` and ``merge`` files are read from the
commit last fetched into the git plugin's store, so they are what the source
holds even before its clone moves; a local folder is read as it stands,
because its owner edits there. Both refuse a path that escapes the source.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.sdk import UntapedError
from untaped_dotfiles.errors import DotfilesError, GitError
from untaped_dotfiles.models import TreeFile

if TYPE_CHECKING:
    from untaped_git.api import Prefetched, RepoStore


class StoreTree:
    """The files of one commit of a store repo (a git source as last fetched).

    One ``ls-tree`` lists the whole tree on first use; the first read fetches
    the commit's missing blobs in one round trip.
    """

    def __init__(self, store: RepoStore, commit: str) -> None:
        self._store = store
        self._commit = commit
        self._entries: dict[str, TreeFile | None] | None = None
        """Every path in the tree: a :class:`TreeFile` for a blob, ``None`` for a directory."""
        self._reader: Prefetched | None = None

    @property
    def commit(self) -> str | None:
        return self._commit

    def _tree(self) -> dict[str, TreeFile | None]:
        if self._entries is None:
            entries: dict[str, TreeFile | None] = {}
            for entry in self._store.ls_tree(self._commit):
                if entry.type == "blob":
                    entries[entry.path] = TreeFile(
                        path=entry.path, executable=entry.mode == "100755"
                    )
                    parent = entry.path.rpartition("/")[0]
                    while parent and parent not in entries:  # ls-tree -r lists blobs only
                        entries[parent] = None
                        parent = parent.rpartition("/")[0]
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
        if self._reader is None:
            self._reader = self._store.prefetched(trees=[self._commit])
        try:  # the handle is kept on self, which lint's reader cannot follow
            argv = ["show", f"{self._commit}:{path}"]
            return self._reader.run(argv, capture=True).stdout  # untaped: allow blob-reader
        except UntapedError as exc:
            raise GitError(f"reading {path} at {self._commit[:12]} failed: {exc}") from exc


class WorkingTree:
    """The files of a local folder (``.git`` left out)."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    @property
    def commit(self) -> str | None:
        return None

    def resolve(self, path: str) -> Path:
        """The absolute path of ``path``; refuses one that leaves the repo (symlinks included)."""
        candidate = (self._root / path).resolve()
        if candidate != self._root and self._root not in candidate.parents:
            raise DotfilesError(f"path {path!r} leaves the source at {self._root}")
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
