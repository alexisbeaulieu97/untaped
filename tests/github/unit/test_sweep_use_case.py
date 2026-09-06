from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import pytest

from untaped.api import ConfigError
from untaped.capabilities.github.application import (
    RepositoryInventoryItem,
    RepositoryInventoryScope,
    TeamScope,
)
from untaped.capabilities.github.application.sweep import Sweep, SweepOptions
from untaped.capabilities.github.domain import (
    CorpusFailure,
    CorpusFreshness,
    CorpusRepoResult,
    CorpusRepoTarget,
    GrepHit,
    RefSelector,
    SweepQuery,
)
from untaped.capabilities.github.domain.errors import GitCorpusError


def _item(full_name: str, *, archived: bool = False) -> RepositoryInventoryItem:
    return RepositoryInventoryItem(
        full_name=full_name,
        name=full_name.rsplit("/", maxsplit=1)[-1],
        html_url=f"https://github.example.com/{full_name}",
        clone_url=f"https://github.example.com/{full_name}.git",
        default_branch="main",
        archived=archived,
    )


def _row(full_name: str, *, archived: bool = False) -> CorpusRepoResult:
    return CorpusRepoResult(
        repo=full_name,
        ref="main",
        path=f"/corpus/{full_name}",
        clone_url=f"https://github.example.com/{full_name}.git",
        fetched_at="2026-07-06T12:00:00+00:00",
        archived=archived,
    )


def _options(
    query: SweepQuery,
    *,
    scope: RepositoryInventoryScope | None = None,
    sync: Literal["auto", "force", "off"] = "auto",
    include_archived: bool = False,
    max_age_seconds: int = 3600,
) -> SweepOptions:
    return SweepOptions(
        scope=scope or RepositoryInventoryScope(),
        stdin_repos=(),
        include_archived=include_archived,
        query=query,
        sync=sync,
        max_age_seconds=max_age_seconds,
        depth=1,
        parallel=1,
        owners=True,
    )


class _Resolver:
    def __init__(self, rows: tuple[RepositoryInventoryItem, ...]) -> None:
        self.rows = rows
        self.scopes: list[RepositoryInventoryScope] = []

    def __call__(self, scope: RepositoryInventoryScope) -> tuple[RepositoryInventoryItem, ...]:
        self.scopes.append(scope)
        return self.rows


@dataclass
class _Synced:
    repo: str
    selector: RefSelector


class _Corpus:
    def __init__(
        self,
        *,
        cached_rows: tuple[CorpusRepoResult, ...] = (),
        freshness: dict[str, CorpusFreshness] | None = None,
        sync_failures: dict[str, str] | None = None,
    ) -> None:
        self.cached_rows = cached_rows
        self.freshness = freshness or {}
        self.sync_failures = sync_failures or {}
        self.synced: list[_Synced] = []
        self.local_ref_map: dict[str, tuple[str, ...]] = {}
        self.tree_map: dict[tuple[str, str], tuple[str, ...]] = {}
        self.grep_map: dict[tuple[str, str, str], tuple[GrepHit, ...]] = {}
        self.blob_map: dict[tuple[str, str, str], str | None] = {}
        self.grep_calls: list[tuple[str, str, str, bool, bool, bool]] = []

    def sync_repo(
        self,
        repo: CorpusRepoTarget,
        *,
        root: Path,
        selector: RefSelector,
        depth: int,
        auth_header: str | None,
    ) -> CorpusRepoResult:
        self.synced.append(_Synced(repo.full_name, selector))
        reason = self.sync_failures.get(repo.full_name)
        if reason is not None:
            raise GitCorpusError(reason)
        fetched_at = datetime(2026, 7, 6, 12, tzinfo=UTC).isoformat()
        self.freshness[repo.full_name] = CorpusFreshness(
            fetched_at=datetime.fromisoformat(fetched_at),
            profile=selector.profile,
            ref_globs=selector.globs,
            archived=repo.archived,
        )
        return CorpusRepoResult(
            repo=repo.full_name,
            ref=repo.default_branch or "main",
            path=str(root / repo.full_name.replace("/", "__")),
            clone_url=repo.clone_url,
            fetched_at=fetched_at,
            profile=selector.profile,
            ref_globs=selector.globs,
            archived=repo.archived,
        )

    def repo_freshness(self, repo: CorpusRepoTarget, *, root: Path) -> CorpusFreshness | None:
        return self.freshness.get(repo.full_name)

    def local_refs(
        self,
        repo: CorpusRepoTarget,
        *,
        root: Path,
        selector: RefSelector,
    ) -> tuple[str, ...]:
        return self.local_ref_map.get(repo.full_name, ("main",))

    def grep_ref(
        self,
        repo: CorpusRepoTarget,
        *,
        root: Path,
        ref: str,
        pattern: str,
        paths: tuple[str, ...],
        ignore_case: bool,
        fixed_strings: bool,
        word_regexp: bool,
    ) -> tuple[GrepHit, ...]:
        self.grep_calls.append(
            (repo.full_name, ref, pattern, ignore_case, fixed_strings, word_regexp)
        )
        return self.grep_map.get((repo.full_name, ref, pattern), ())

    def tree_paths(self, repo: CorpusRepoTarget, *, root: Path, ref: str) -> tuple[str, ...]:
        return self.tree_map.get((repo.full_name, ref), ())

    def read_blob(
        self,
        repo: CorpusRepoTarget,
        *,
        root: Path,
        ref: str,
        path: str,
    ) -> str | None:
        return self.blob_map.get((repo.full_name, ref, path))

    def list_repos(self, *, root: Path) -> tuple[CorpusRepoResult, ...]:
        return self.cached_rows


def _sweep(corpus: _Corpus, resolver: _Resolver, root: Path) -> Sweep:
    return Sweep(
        inventory=resolver,
        corpus=corpus,
        root=root,
        auth_header=lambda: "AUTHORIZATION: basic token",
    )


def test_offline_scope_from_corpus_metadata(tmp_path: Path) -> None:
    corpus = _Corpus(
        cached_rows=(
            _row("acme/api"),
            _row("acme/archived", archived=True),
            _row("other/api"),
        )
    )
    corpus.tree_map[("acme/api", "main")] = ("README.md",)

    report = _sweep(corpus, _Resolver(()), tmp_path / "corpus")(
        _options(
            SweepQuery(has_files=("README.md",)),
            scope=RepositoryInventoryScope(orgs=("acme",)),
            sync="off",
        )
    )

    assert [row.full_name for row in report.rows] == ["acme/api"]
    assert report.rows[0].clone_url == "https://github.example.com/acme/api.git"
    assert report.scanned == 1
    assert report.refreshed == 0
    assert report.cached == 1


def test_offline_team_scope_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="--team requires the API"):
        _sweep(_Corpus(), _Resolver(()), tmp_path / "corpus")(
            _options(
                SweepQuery(has_files=("README.md",)),
                scope=RepositoryInventoryScope(teams=(TeamScope(org="acme", slug="ops"),)),
                sync="off",
            )
        )


def test_offline_empty_scope_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="corpus has no repos in scope"):
        _sweep(_Corpus(cached_rows=(_row("other/api"),)), _Resolver(()), tmp_path / "corpus")(
            _options(
                SweepQuery(has_files=("README.md",)),
                scope=RepositoryInventoryScope(orgs=("acme",)),
                sync="off",
            )
        )


def test_auto_sync_refreshes_only_stale_or_underprofiled(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    corpus = _Corpus(
        freshness={
            "acme/fresh": CorpusFreshness(fetched_at=now, profile="branches"),
            "acme/stale": CorpusFreshness(
                fetched_at=now - timedelta(seconds=7200), profile="branches"
            ),
            "acme/under": CorpusFreshness(fetched_at=now, profile="default"),
        }
    )
    for name in ("acme/fresh", "acme/stale", "acme/under", "acme/new"):
        corpus.tree_map[(name, "main")] = ("README.md",)
    resolver = _Resolver(
        tuple(_item(name) for name in ("acme/fresh", "acme/new", "acme/stale", "acme/under"))
    )

    report = _sweep(corpus, resolver, tmp_path / "corpus")(
        _options(
            SweepQuery(has_files=("README.md",), refs=RefSelector(profile="branches")),
            scope=RepositoryInventoryScope(orgs=("acme",)),
        )
    )

    assert [synced.repo for synced in corpus.synced] == [
        "acme/new",
        "acme/stale",
        "acme/under",
    ]
    assert report.scanned == 4
    assert report.refreshed == 3
    assert report.cached == 1


def test_failed_refresh_with_covering_cache_scans_cached(tmp_path: Path) -> None:
    corpus = _Corpus(
        freshness={
            "acme/api": CorpusFreshness(fetched_at=datetime.now(UTC), profile="default"),
        },
        sync_failures={"acme/api": "fetch denied"},
    )
    corpus.tree_map[("acme/api", "main")] = ("README.md",)

    report = _sweep(corpus, _Resolver((_item("acme/api"),)), tmp_path / "corpus")(
        _options(
            SweepQuery(has_files=("README.md",)),
            scope=RepositoryInventoryScope(repos=("acme/api",)),
            sync="force",
        )
    )

    assert [row.full_name for row in report.rows] == ["acme/api"]
    assert report.unscanned == ()
    assert report.scanned == 1
    assert report.refreshed == 0
    assert report.cached == 1


def test_failed_refresh_without_cache_is_unscanned(tmp_path: Path) -> None:
    corpus = _Corpus(sync_failures={"acme/api": "fetch denied"})

    report = _sweep(corpus, _Resolver((_item("acme/api"),)), tmp_path / "corpus")(
        _options(
            SweepQuery(has_files=("README.md",)),
            scope=RepositoryInventoryScope(repos=("acme/api",)),
            sync="force",
        )
    )

    assert report.rows == ()
    assert report.unscanned == (CorpusFailure(repo="acme/api", reason="fetch denied"),)
    assert report.scanned == 0


def test_repo_matches_when_any_ref_matches(tmp_path: Path) -> None:
    corpus = _Corpus()
    corpus.local_ref_map["acme/api"] = ("main", "release/1")
    corpus.tree_map[("acme/api", "release/1")] = ("README.md",)

    report = _sweep(corpus, _Resolver((_item("acme/api"),)), tmp_path / "corpus")(
        _options(
            SweepQuery(has_files=("README.md",), refs=RefSelector(globs=("release/*",))),
            scope=RepositoryInventoryScope(repos=("acme/api",)),
        )
    )

    assert report.rows[0].refs_matched == ("release/1",)
    assert report.rows[0].clone_url == "https://github.example.com/acme/api.git"


def test_identical_blob_across_refs_yields_one_match_row(tmp_path: Path) -> None:
    corpus = _Corpus()
    corpus.local_ref_map["acme/api"] = ("main", "release/1")
    corpus.grep_map[("acme/api", "main", "needle")] = (
        GrepHit(path="app.py", line=3, text="needle()", blob_oid="abc123"),
    )
    corpus.grep_map[("acme/api", "release/1", "needle")] = (
        GrepHit(path="app.py", line=3, text="needle()", blob_oid="abc123"),
    )

    report = _sweep(corpus, _Resolver((_item("acme/api"),)), tmp_path / "corpus")(
        _options(
            SweepQuery(greps=("needle",), refs=RefSelector(globs=("release/*",))),
            scope=RepositoryInventoryScope(repos=("acme/api",)),
        )
    )

    assert len(report.matches) == 1
    assert report.matches[0].full_name == "acme/api"
    assert report.matches[0].refs == ("main", "release/1")
    assert report.matches[0].path == "app.py"


def test_owners_from_matched_paths(tmp_path: Path) -> None:
    corpus = _Corpus()
    corpus.grep_map[("acme/api", "main", "needle")] = (
        GrepHit(path="src/app.py", line=1, text="needle()", blob_oid="abc123"),
    )
    corpus.tree_map[("acme/api", "main")] = ("src/app.yml",)
    corpus.blob_map[("acme/api", "main", ".github/CODEOWNERS")] = "* @all\nsrc/ @src\n*.py @py\n"

    report = _sweep(corpus, _Resolver((_item("acme/api"),)), tmp_path / "corpus")(
        _options(
            SweepQuery(greps=("needle",), has_files=("src/*.yml",)),
            scope=RepositoryInventoryScope(repos=("acme/api",)),
        )
    )

    assert report.rows[0].owners == ("@py", "@src")


def test_pathless_match_uses_default_owners(tmp_path: Path) -> None:
    corpus = _Corpus()
    corpus.blob_map[("acme/api", "main", ".github/CODEOWNERS")] = "* @all\n"

    report = _sweep(corpus, _Resolver((_item("acme/api"),)), tmp_path / "corpus")(
        _options(
            SweepQuery(not_greps=("needle",)),
            scope=RepositoryInventoryScope(repos=("acme/api",)),
        )
    )

    assert report.rows[0].owners == ("@all",)


def test_missing_codeowners_is_empty(tmp_path: Path) -> None:
    corpus = _Corpus()
    corpus.tree_map[("acme/api", "main")] = ("README.md",)

    report = _sweep(corpus, _Resolver((_item("acme/api"),)), tmp_path / "corpus")(
        _options(
            SweepQuery(has_files=("README.md",)),
            scope=RepositoryInventoryScope(repos=("acme/api",)),
        )
    )

    assert report.rows[0].owners == ()


def test_archived_repos_excluded_by_default(tmp_path: Path) -> None:
    corpus = _Corpus()
    corpus.tree_map[("acme/api", "main")] = ("README.md",)
    corpus.tree_map[("acme/old", "main")] = ("README.md",)

    report = _sweep(
        corpus,
        _Resolver((_item("acme/api"), _item("acme/old", archived=True))),
        tmp_path / "corpus",
    )(
        _options(
            SweepQuery(has_files=("README.md",)),
            scope=RepositoryInventoryScope(orgs=("acme",)),
        )
    )

    assert [synced.repo for synced in corpus.synced] == ["acme/api"]
    assert [row.full_name for row in report.rows] == ["acme/api"]
