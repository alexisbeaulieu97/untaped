"""Use cases for local Git corpus cache lifecycle workflows."""

from __future__ import annotations

from pathlib import Path

from untaped.sdk import dir_bytes
from untaped_github.application.inventory import split_full_name
from untaped_github.application.ports import GitCorpus
from untaped_github.errors import GitCorpusError
from untaped_github.records import CacheDeleteOutcome, CorpusRepo, Worktree


class StatusCorpus:
    """List repositories cached in the local corpus."""

    def __init__(self, corpus: GitCorpus) -> None:
        self._corpus = corpus

    def __call__(self) -> tuple[CorpusRepo, ...]:
        return tuple(with_disk_bytes(row) for row in self._corpus.list_repos())


class CleanCorpus:
    """Release repositories from the corpus: each goes from disk unless another plugin uses it.

    The outcome is ``deleted`` with the bytes it freed in ``disk_bytes``, or
    ``released`` with who kept the repo in ``kept``.
    """

    def __init__(self, corpus: GitCorpus) -> None:
        self._corpus = corpus

    def __call__(self, *, repo: CorpusRepo) -> CacheDeleteOutcome:
        return self._corpus.clean_repo(repo)


class WorktreeCorpus:
    """Materialize one cached repository ref as a worktree."""

    def __init__(self, corpus: GitCorpus) -> None:
        self._corpus = corpus

    def __call__(self, repo: str, *, ref: str | None) -> Worktree:
        split_full_name(repo)
        item = self._corpus.get_repo(repo)
        if item is None:
            raise GitCorpusError("repository is not in the local corpus")
        return self._corpus.materialize_worktree(item, ref=ref)


def with_disk_bytes(row: CorpusRepo) -> CorpusRepo:
    """``row`` with ``disk_bytes`` measured from its store repository on disk."""
    return row.model_copy(update={"disk_bytes": dir_bytes(Path(row.path))})
