"""Unit tests for local corpus cache lifecycle use cases."""

from __future__ import annotations

from pathlib import Path

import pytest

from untaped.capabilities.github.application.cache import CleanCorpus, StatusCorpus, WorktreeCorpus
from untaped.capabilities.github.domain import CorpusRepoResult, WorktreeResult
from untaped.capabilities.github.domain.errors import GitCorpusError


class _Corpus:
    def __init__(self, *, cached: set[str] | None = None) -> None:
        self.cached = cached or set()

    def list_repos(self, *, root: Path) -> tuple[CorpusRepoResult, ...]:
        return tuple(
            CorpusRepoResult(
                repo=name,
                ref="main",
                path=str(root / name.replace("/", "__")),
                status="cached",
                fetched_at="2026-07-06T12:00:00+00:00",
                profile="branches",
            )
            for name in sorted(self.cached)
        )

    def get_repo(self, *, root: Path, repo: str) -> object | None:
        if repo not in self.cached:
            return None
        return type(
            "Repo",
            (),
            {
                "full_name": repo,
                "default_branch": "main",
                "clone_url": f"https://github.com/{repo}.git",
                "html_url": f"https://github.com/{repo}",
            },
        )()

    def clean_repo(self, *, root: Path, repo: CorpusRepoResult) -> CorpusRepoResult:
        self.cached.discard(repo.repo)
        return repo.model_copy(update={"status": "removed"})

    def materialize_worktree(
        self,
        repo: object,
        *,
        root: Path,
        ref: str | None,
    ) -> WorktreeResult:
        return WorktreeResult(
            repo=repo.full_name,  # type: ignore[attr-defined]
            ref=ref or repo.default_branch,  # type: ignore[attr-defined]
            path=str(root / "worktrees" / repo.full_name.replace("/", "__")),  # type: ignore[attr-defined]
        )


def test_status_corpus_reports_profile_and_disk_bytes(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    repo_path = root / "acme__api"
    repo_path.mkdir(parents=True)
    (repo_path / "payload").write_bytes(b"12345")

    rows = StatusCorpus(_Corpus(cached={"acme/api"}))(root=root)

    assert rows[0].repo == "acme/api"
    assert rows[0].profile == "branches"
    assert rows[0].disk_bytes >= 5


def test_list_clean_and_worktree_delegate_to_corpus(tmp_path: Path) -> None:
    corpus = _Corpus(cached={"acme/api", "acme/worker"})

    listed = StatusCorpus(corpus)(root=tmp_path / "corpus")
    cleaned = CleanCorpus(corpus)(root=tmp_path / "corpus", repo=listed[0])
    worktree = WorktreeCorpus(corpus)(
        "acme/worker",
        root=tmp_path / "corpus",
        ref="main",
    )

    assert [row.repo for row in listed] == ["acme/api", "acme/worker"]
    assert cleaned.repo == "acme/api"
    assert worktree == WorktreeResult(
        repo="acme/worker",
        ref="main",
        path=str(tmp_path / "corpus" / "worktrees" / "acme__worker"),
    )


def test_worktree_missing_cached_repo_is_actionable(tmp_path: Path) -> None:
    with pytest.raises(GitCorpusError, match="local corpus"):
        WorktreeCorpus(_Corpus())("acme/missing", root=tmp_path / "corpus", ref=None)
