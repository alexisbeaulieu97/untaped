"""Use cases for local Git corpus cache lifecycle workflows."""

from __future__ import annotations

from pathlib import Path

from untaped.api import UntapedError
from untaped.capabilities.github.application.ports import GitCorpus
from untaped.capabilities.github.domain import CorpusRepoResult, WorktreeResult
from untaped.capabilities.github.domain.errors import GitCorpusError


class StatusCorpus:
    """List repositories cached in the local corpus."""

    def __init__(self, corpus: GitCorpus) -> None:
        self._corpus = corpus

    def __call__(self, *, root: Path) -> tuple[CorpusRepoResult, ...]:
        return tuple(_with_disk_bytes(row) for row in self._corpus.list_repos(root=root))


class CleanCorpus:
    """Remove repositories from the managed local corpus."""

    def __init__(self, corpus: GitCorpus) -> None:
        self._corpus = corpus

    def __call__(self, *, root: Path, repo: CorpusRepoResult) -> CorpusRepoResult:
        return self._corpus.clean_repo(root=root, repo=repo)


class WorktreeCorpus:
    """Materialize one cached repository ref as a worktree."""

    def __init__(self, corpus: GitCorpus) -> None:
        self._corpus = corpus

    def __call__(self, repo: str, *, root: Path, ref: str | None) -> WorktreeResult:
        owner, separator, name = repo.partition("/")
        if not owner or not separator or not name or "/" in name:
            raise UntapedError(f"repository must be owner/name: {repo!r}")
        item = self._corpus.get_repo(root=root, repo=repo)
        if item is None:
            raise GitCorpusError("repository is not in the local corpus")
        return self._corpus.materialize_worktree(item, root=root, ref=ref)


def _with_disk_bytes(row: CorpusRepoResult) -> CorpusRepoResult:
    return row.model_copy(update={"disk_bytes": _disk_bytes(Path(row.path))})


def _disk_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            total += child.stat().st_size
    return total
