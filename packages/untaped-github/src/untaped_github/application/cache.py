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

    def __call__(self) -> tuple[CorpusRepoResult, ...]:
        return tuple(with_disk_bytes(row) for row in self._corpus.list_repos())


class CleanCorpus:
    """Release repositories from the corpus: each goes from disk unless another plugin uses it.

    The row says ``removed`` with the bytes it freed in ``disk_bytes``, or
    ``released`` with who kept the repo in ``kept``.
    """

    def __init__(self, corpus: GitCorpus) -> None:
        self._corpus = corpus

    def __call__(self, *, repo: CorpusRepoResult) -> CorpusRepoResult:
        return self._corpus.clean_repo(repo)


class WorktreeCorpus:
    """Materialize one cached repository ref as a worktree."""

    def __init__(self, corpus: GitCorpus) -> None:
        self._corpus = corpus

    def __call__(self, repo: str, *, ref: str | None) -> WorktreeResult:
        split_full_name(repo)
        item = self._corpus.get_repo(repo)
        if item is None:
            raise GitCorpusError("repository is not in the local corpus")
        return self._corpus.materialize_worktree(item, ref=ref)


def with_disk_bytes(row: CorpusRepoResult) -> CorpusRepoResult:
    """``row`` with ``disk_bytes`` measured from its store repository on disk."""
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
