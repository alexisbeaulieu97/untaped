"""Use cases for local Git corpus cache lifecycle workflows."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from untaped_github.application.inventory import split_full_name
from untaped_github.application.ports import GitCorpus
from untaped_github.domain import CorpusRepoResult, WorktreeResult
from untaped_github.errors import GitCorpusError


class StatusCorpus:
    """List repositories cached in the local corpus."""

    def __init__(self, corpus: GitCorpus) -> None:
        self._corpus = corpus

    def __call__(self, *, root: Path) -> tuple[CorpusRepoResult, ...]:
        return tuple(with_disk_bytes(row) for row in self._corpus.list_repos(root=root))


class CleanCorpus:
    """Remove repositories from the managed local corpus.

    The removed row keeps the ``disk_bytes`` of the ``repo`` it was given (the
    space the removal freed; see :func:`with_disk_bytes`).
    """

    def __init__(self, corpus: GitCorpus) -> None:
        self._corpus = corpus

    def __call__(self, *, root: Path, repo: CorpusRepoResult) -> CorpusRepoResult:
        removed = self._corpus.clean_repo(root=root, repo=repo)
        return removed.model_copy(update={"disk_bytes": repo.disk_bytes})


class WorktreeCorpus:
    """Materialize one cached repository ref as a worktree."""

    def __init__(self, corpus: GitCorpus) -> None:
        self._corpus = corpus

    def __call__(self, repo: str, *, root: Path, ref: str | None) -> WorktreeResult:
        split_full_name(repo)
        item = self._corpus.get_repo(root=root, repo=repo)
        if item is None:
            raise GitCorpusError("repository is not in the local corpus")
        return self._corpus.materialize_worktree(item, root=root, ref=ref)


def with_disk_bytes(row: CorpusRepoResult) -> CorpusRepoResult:
    """``row`` with ``disk_bytes`` measured from its bare repository on disk."""
    return row.model_copy(update={"disk_bytes": _disk_bytes(Path(row.path))})


def _disk_bytes(path: Path) -> int:
    """Bytes of the regular files under ``path``, the space deleting it frees.

    Links count as themselves, not what they point at, and a file that
    vanishes or cannot be read while measuring is skipped.
    """
    total = 0
    for directory, _, files in os.walk(path):
        for name in files:
            try:
                info = os.lstat(os.path.join(directory, name))
            except OSError:
                continue
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
    return total
