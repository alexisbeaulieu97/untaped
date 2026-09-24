"""Sweep and SyncCorpus use cases over an in-memory corpus fake."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from untaped.capabilities.github.application import (
    RepositoryInventoryItem,
    RepositoryInventoryScope,
    TeamScope,
)
from untaped.capabilities.github.application.sweep import (
    CorpusSyncOptions,
    Sweep,
    SweepOptions,
    SweepReport,
    SyncCorpus,
)
from untaped.capabilities.github.domain import (
    CorpusFailure,
    CorpusFreshness,
    CorpusRepoResult,
    CorpusRepoTarget,
    GrepHit,
    GrepSpec,
    LocalRef,
    RefSelector,
    SweepQuery,
)
from untaped.capabilities.github.domain.errors import GitCorpusError
from untaped.capability_api import ConfigError, HttpStatusError, UntapedError, UsageError

README = SweepQuery(has_files=("README.md",))


def _item(
    full_name: str, *, archived: bool = False, pushed_at: str | None = None
) -> RepositoryInventoryItem:
    return RepositoryInventoryItem(
        full_name=full_name,
        name=full_name.rsplit("/", maxsplit=1)[-1],
        html_url=f"https://github.example.com/{full_name}",
        clone_url=f"https://github.example.com/{full_name}.git",
        default_branch="main",
        archived=archived,
        pushed_at=pushed_at,
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


class _Resolver:
    """Inventory stub: every scope resolves to ``rows``; names in ``missing`` raise a 404."""

    def __init__(self, *rows: RepositoryInventoryItem, missing: frozenset[str] = frozenset()):
        self.rows = rows
        self.missing = missing
        self.scopes: list[RepositoryInventoryScope] = []

    def __call__(self, scope: RepositoryInventoryScope) -> tuple[RepositoryInventoryItem, ...]:
        self.scopes.append(scope)
        for name in set(scope.repos) & self.missing:
            raise UntapedError(f"failed to expand repository {name}: 404 Not Found")
        wanted = set(scope.repos)
        return tuple(row for row in self.rows if not wanted or row.full_name in wanted)


class _Corpus:
    """In-memory corpus: each ref's tree is its own name unless ``ref_trees`` says otherwise."""

    def __init__(
        self,
        *,
        cached_rows: tuple[CorpusRepoResult, ...] = (),
        freshness: dict[str, CorpusFreshness] | None = None,
        sync_errors: dict[str, Exception] | None = None,
    ) -> None:
        self.cached_rows = cached_rows
        self.freshness = freshness or {}
        self.sync_errors = sync_errors or {}
        self.freshness_errors: dict[str, str] = {}
        self.synced: list[str] = []
        self.touched: list[str] = []
        self.local_ref_map: dict[str, tuple[str, ...]] = {}
        self.ref_trees: dict[tuple[str, str], str] = {}
        self.tree_map: dict[tuple[str, str], tuple[str, ...]] = {}
        self.grep_map: dict[tuple[str, str, str], tuple[GrepHit, ...]] = {}
        self.blob_map: dict[tuple[str, str, str], str | None] = {}
        self.grep_calls: list[tuple[tuple[str, ...], str]] = []
        self.exists_calls: list[tuple[str, str, str]] = []

    def sync_repo(
        self,
        repo: CorpusRepoTarget,
        *,
        root: Path,
        selector: RefSelector,
        depth: int,
        auth_header: str | None,
    ) -> CorpusRepoResult:
        self.synced.append(repo.full_name)
        error = self.sync_errors.get(repo.full_name)
        if error is not None:
            raise error
        fetched_at = datetime(2026, 7, 6, 12, tzinfo=UTC)
        self.freshness[repo.full_name] = CorpusFreshness(
            fetched_at=fetched_at, profile=selector.profile, ref_globs=selector.globs
        )
        return CorpusRepoResult(
            repo=repo.full_name, ref="main", path=str(root), fetched_at=fetched_at.isoformat()
        )

    def repo_freshness(self, repo: CorpusRepoTarget, *, root: Path) -> CorpusFreshness | None:
        reason = self.freshness_errors.get(repo.full_name)
        if reason is not None:
            raise GitCorpusError(reason)
        return self.freshness.get(repo.full_name)

    def touch_repo(self, repo: CorpusRepoTarget, *, root: Path) -> datetime:
        self.touched.append(repo.full_name)
        return datetime(2026, 7, 7, 12, tzinfo=UTC)

    def local_refs(
        self, repo: CorpusRepoTarget, *, root: Path, selector: RefSelector
    ) -> tuple[LocalRef, ...]:
        names = self.local_ref_map.get(repo.full_name, ("main",))
        return tuple(
            LocalRef(name=name, tree=self.ref_trees.get((repo.full_name, name), name))
            for name in names
        )

    def grep_trees(
        self, repo: CorpusRepoTarget, *, root: Path, trees: tuple[str, ...], spec: GrepSpec
    ) -> dict[str, tuple[GrepHit, ...]]:
        self.grep_calls.append((trees, spec.pattern))
        found = {tree: self.grep_map.get((repo.full_name, tree, spec.pattern)) for tree in trees}
        return {tree: hits for tree, hits in found.items() if hits}

    def tree_has_match(
        self, repo: CorpusRepoTarget, *, root: Path, tree: str, spec: GrepSpec
    ) -> bool:
        self.exists_calls.append((repo.full_name, tree, spec.pattern))
        return bool(self.grep_map.get((repo.full_name, tree, spec.pattern)))

    def tree_paths(self, repo: CorpusRepoTarget, *, root: Path, ref: str) -> tuple[str, ...]:
        return self.tree_map.get((repo.full_name, ref), ())

    def read_first_blob(
        self, repo: CorpusRepoTarget, *, root: Path, ref: str, paths: tuple[str, ...]
    ) -> str | None:
        found = (self.blob_map.get((repo.full_name, ref, path)) for path in paths)
        return next((text for text in found if text is not None), None)

    def list_repos(self, *, root: Path) -> tuple[CorpusRepoResult, ...]:
        return self.cached_rows


def _readme(corpus: _Corpus, *names: str) -> _Corpus:
    for name in names:
        corpus.tree_map[(name, "main")] = ("README.md",)
    return corpus


def _sweep(
    corpus: _Corpus,
    query: SweepQuery = README,
    *,
    resolver: _Resolver | None = None,
    progress: Any = None,
    **options: Any,
) -> SweepReport:
    defaults: dict[str, Any] = {
        "scope": RepositoryInventoryScope(orgs=("acme",)),
        "stdin_repos": (),
        "include_archived": False,
        "sync": "auto",
        "max_age_seconds": 3600,
        "depth": 1,
        "parallel": 1,
        "owners": True,
    }
    return Sweep(
        inventory=resolver or _Resolver(),
        corpus=corpus,
        root=Path("/corpus"),
        auth_header=lambda: "AUTHORIZATION: basic token",
    )(SweepOptions(query=query, **{**defaults, **options}), progress=progress)


def _names(report: SweepReport) -> list[str]:
    return [row.full_name for row in report.rows]


_CACHED = (_row("acme/api"), _row("acme/old", archived=True), _row("Other/Tool"), _row("zed/x"))


@pytest.mark.parametrize(
    ("scope", "include_archived", "expected"),
    [
        (RepositoryInventoryScope(orgs=("acme",)), False, ["acme/api"]),
        (RepositoryInventoryScope(orgs=("ACME", "other")), False, ["Other/Tool", "acme/api"]),
        (RepositoryInventoryScope(repos=("Acme/API",)), False, ["acme/api"]),
        (RepositoryInventoryScope(orgs=("acme",)), True, ["acme/api", "acme/old"]),
    ],
)
def test_cached_sweep_scopes_corpus_rows_case_insensitively(
    scope: RepositoryInventoryScope, include_archived: bool, expected: list[str]
) -> None:
    corpus = _readme(_Corpus(cached_rows=_CACHED), *(row.repo for row in _CACHED))

    report = _sweep(corpus, scope=scope, include_archived=include_archived, sync="off")

    assert _names(report) == expected
    assert report.rows[0].clone_url == f"https://github.example.com/{expected[0]}.git"
    assert (report.scanned, report.refreshed, report.cached) == (len(expected), 0, len(expected))
    assert corpus.synced == []


@pytest.mark.parametrize(
    ("scope", "error", "message"),
    [
        (
            RepositoryInventoryScope(teams=(TeamScope(org="acme", slug="ops"),)),
            UsageError,
            "--team requires the API",
        ),
        (RepositoryInventoryScope(orgs=("nobody",)), ConfigError, "corpus has no repos in scope"),
    ],
)
def test_cached_sweep_rejects_unusable_scopes(
    scope: RepositoryInventoryScope, error: type[Exception], message: str
) -> None:
    with pytest.raises(error, match=message):
        _sweep(_Corpus(cached_rows=_CACHED), scope=scope, sync="off")


def test_auto_sync_refreshes_only_stale_or_underprofiled() -> None:
    now = datetime.now(UTC)
    corpus = _Corpus(
        freshness={
            "acme/fresh": CorpusFreshness(fetched_at=now, profile="branches"),
            "acme/stale": CorpusFreshness(fetched_at=now - timedelta(hours=2), profile="branches"),
            "acme/under": CorpusFreshness(fetched_at=now, profile="default"),
        }
    )
    names = ("acme/fresh", "acme/new", "acme/stale", "acme/under")

    report = _sweep(
        _readme(corpus, *names),
        SweepQuery(has_files=("README.md",), refs=RefSelector(profile="branches")),
        resolver=_Resolver(*map(_item, names)),
    )

    assert corpus.synced == ["acme/new", "acme/stale", "acme/under"]
    assert (report.scanned, report.refreshed, report.cached) == (4, 3, 1)


def test_failed_refresh_with_covering_cache_scans_cached() -> None:
    corpus = _Corpus(
        freshness={"acme/api": CorpusFreshness(fetched_at=datetime.now(UTC), profile="default")},
        sync_errors={"acme/api": GitCorpusError("fetch denied")},
    )

    report = _sweep(
        _readme(corpus, "acme/api"), resolver=_Resolver(_item("acme/api")), sync="force"
    )

    assert _names(report) == ["acme/api"]
    assert report.unscanned == ()
    assert report.stale == (CorpusFailure(repo="acme/api", reason="fetch denied"),)
    assert (report.scanned, report.refreshed, report.cached) == (1, 0, 1)


def _unresolvable(corpus: _Corpus) -> dict[str, Any]:
    return {
        "resolver": _Resolver(_item("acme/api"), missing=frozenset({"acme/bad"})),
        "scope": RepositoryInventoryScope(repos=("acme/bad", "acme/api")),
    }


def _corrupt_metadata(corpus: _Corpus) -> dict[str, Any]:
    corpus.freshness_errors["acme/bad"] = "could not read corpus metadata: invalid JSON"
    return {"resolver": _Resolver(_item("acme/api"), _item("acme/bad"))}


def _corrupt_cached_metadata(corpus: _Corpus) -> dict[str, Any]:
    corpus.cached_rows = (_row("acme/api"), _row("acme/bad"))
    corpus.freshness_errors["acme/bad"] = "invalid fetched_at"
    return {"sync": "off"}


def _sync_error(error: Exception) -> Callable[[_Corpus], dict[str, Any]]:
    def setup(corpus: _Corpus) -> dict[str, Any]:
        corpus.sync_errors["acme/bad"] = error
        return {"resolver": _Resolver(_item("acme/api"), _item("acme/bad")), "sync": "force"}

    return setup


@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        (_unresolvable, "failed to expand repository acme/bad: 404 Not Found"),
        (_corrupt_metadata, "could not read corpus metadata: invalid JSON"),
        (_corrupt_cached_metadata, "invalid fetched_at"),
        (_sync_error(PermissionError("permission denied: corpus")), "permission denied: corpus"),
        (_sync_error(GitCorpusError("fetch denied")), "fetch denied"),
    ],
    ids=["unresolvable", "corrupt-metadata", "corrupt-cached-metadata", "os-error", "no-cache"],
)
def test_one_broken_repo_is_unscanned_not_fatal(
    setup: Callable[[_Corpus], dict[str, Any]], reason: str
) -> None:
    corpus = _readme(_Corpus(), "acme/api")

    report = _sweep(corpus, **setup(corpus))

    assert _names(report) == ["acme/api"]
    assert report.unscanned == (CorpusFailure(repo="acme/bad", reason=reason),)


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (401, '{"message": "Bad credentials"}'),
        (429, '{"message": "Too many requests"}'),
        (403, '{"message": "API rate limit exceeded for 1.2.3.4."}'),
    ],
)
def test_auth_and_rate_limit_failures_abort_instead_of_unscanned(status: int, body: str) -> None:
    # Bad credentials and rate limits fail every repo alike: the first one
    # aborts the sweep instead of turning each repo into an unscanned row.
    calls: list[RepositoryInventoryScope] = []

    def resolver(scope: RepositoryInventoryScope) -> tuple[RepositoryInventoryItem, ...]:
        calls.append(scope)
        cause = HttpStatusError(f"HTTP {status}", status_code=status, body=body)
        raise UntapedError(f"failed to expand repository {scope.repos[0]}") from cause

    with pytest.raises(UntapedError):
        _sweep(
            _Corpus(),
            resolver=resolver,  # type: ignore[arg-type]
            scope=RepositoryInventoryScope(repos=("acme/a", "acme/b")),
        )

    assert len(calls) == 1


def test_repo_matches_when_any_selected_ref_matches() -> None:
    corpus = _Corpus()
    corpus.local_ref_map["acme/api"] = ("main", "release/1")
    corpus.tree_map[("acme/api", "release/1")] = ("README.md",)

    report = _sweep(
        corpus,
        SweepQuery(has_files=("README.md",), refs=RefSelector(globs=("release/*",))),
        resolver=_Resolver(_item("acme/api")),
    )

    assert report.rows[0].refs_matched == ("release/1",)


def test_identical_line_across_refs_yields_one_match_row() -> None:
    corpus = _Corpus()
    corpus.local_ref_map["acme/api"] = ("main", "release/1")
    for ref in ("main", "release/1"):
        corpus.grep_map[("acme/api", ref, "needle")] = (GrepHit("app.py", 3, "needle()"),)

    report = _sweep(
        corpus,
        SweepQuery(greps=("needle",), refs=RefSelector(globs=("release/*",))),
        resolver=_Resolver(_item("acme/api")),
    )

    [match] = report.matches
    assert (match.full_name, match.refs, match.path) == (
        "acme/api",
        ("main", "release/1"),
        "app.py",
    )


def test_refs_sharing_a_tree_are_grepped_once_and_all_reported() -> None:
    corpus = _Corpus()
    corpus.local_ref_map["acme/api"] = ("main", "v1", "release/1")
    corpus.ref_trees[("acme/api", "v1")] = "main"
    corpus.grep_map[("acme/api", "main", "needle")] = (GrepHit("app.py", 3, "needle()"),)

    report = _sweep(
        corpus,
        SweepQuery(greps=("needle",), refs=RefSelector(profile="all")),
        resolver=_Resolver(_item("acme/api")),
    )

    assert corpus.grep_calls == [(("main", "release/1"), "needle")]
    assert report.rows[0].refs_matched == ("main", "v1")
    assert report.matches[0].refs == ("main", "v1")


@pytest.mark.parametrize(
    ("query", "codeowners", "expected"),
    [
        pytest.param(
            SweepQuery(greps=("needle",), has_files=("src/*.yml",)),
            "* @all\nsrc/ @src\n*.py @py\n",
            ("@py", "@src"),
            id="owners-of-matched-paths",
        ),
        pytest.param(SweepQuery(not_greps=("absent",)), "* @all\n", ("@all",), id="pathless"),
        pytest.param(README, None, (), id="no-codeowners"),
    ],
)
def test_owners_come_from_codeowners_for_matched_paths(
    query: SweepQuery, codeowners: str | None, expected: tuple[str, ...]
) -> None:
    corpus = _Corpus()
    corpus.grep_map[("acme/api", "main", "needle")] = (GrepHit("src/app.py", 1, "needle()"),)
    corpus.tree_map[("acme/api", "main")] = ("src/app.yml", "README.md")
    corpus.blob_map[("acme/api", "refs/heads/main", ".github/CODEOWNERS")] = codeowners

    report = _sweep(corpus, query, resolver=_Resolver(_item("acme/api")))

    assert report.rows[0].owners == expected


def test_archived_repos_excluded_by_default() -> None:
    corpus = _readme(_Corpus(), "acme/api", "acme/old")

    report = _sweep(corpus, resolver=_Resolver(_item("acme/api"), _item("acme/old", archived=True)))

    assert corpus.synced == ["acme/api"]
    assert _names(report) == ["acme/api"]


_PUSHED = "2026-07-01T00:00:00Z"


def _old_copy(*, pushed_at: str | None = _PUSHED, branch: str = "main") -> CorpusFreshness:
    return CorpusFreshness(
        fetched_at=datetime.now(UTC) - timedelta(hours=2),
        profile="default",
        pushed_at=pushed_at,
        default_branch=branch,
    )


def test_expired_copy_with_unchanged_pushed_at_is_touched_not_fetched() -> None:
    corpus = _readme(_Corpus(freshness={"acme/api": _old_copy()}), "acme/api")

    report = _sweep(corpus, resolver=_Resolver(_item("acme/api", pushed_at=_PUSHED)))

    assert corpus.synced == []
    assert corpus.touched == ["acme/api"]
    assert (report.cached, report.refreshed) == (1, 0)
    assert report.rows[0].synced_at == "2026-07-07T12:00:00+00:00"


@pytest.mark.parametrize(
    ("stored", "pushed_at", "sync"),
    [
        (_old_copy(pushed_at="2026-06-01T00:00:00Z"), _PUSHED, "auto"),
        (_old_copy(branch="master"), _PUSHED, "auto"),
        (_old_copy(pushed_at=None), None, "auto"),
        (_old_copy(), _PUSHED, "force"),
    ],
    ids=["pushed-since", "default-branch-renamed", "pushed-at-unknown", "refresh"],
)
def test_expired_copy_is_fetched_unless_github_reports_it_unchanged(
    stored: CorpusFreshness, pushed_at: str | None, sync: str
) -> None:
    corpus = _Corpus(freshness={"acme/api": stored})

    _sweep(corpus, resolver=_Resolver(_item("acme/api", pushed_at=pushed_at)), sync=sync)

    assert corpus.synced == ["acme/api"]
    assert corpus.touched == []


@pytest.mark.parametrize(
    ("query", "expected", "hits"),
    [
        (README, ["acme/api"], {"has-file:README.md": 1}),
        (SweepQuery(lacks_files=("README.md",)), ["acme/new"], {"lacks-file:README.md": 0}),
    ],
)
def test_file_predicates_read_each_tree_listing(
    query: SweepQuery, expected: list[str], hits: dict[str, int]
) -> None:
    corpus = _readme(_Corpus(), "acme/api")

    report = _sweep(corpus, query, resolver=_Resolver(_item("acme/api"), _item("acme/new")))

    assert _names(report) == expected
    assert report.rows[0].hits == hits


def test_all_mode_stops_evaluating_a_tree_after_a_failed_predicate() -> None:
    corpus = _Corpus()

    report = _sweep(
        corpus,
        SweepQuery(greps=("first", "second"), not_greps=("third",)),
        resolver=_Resolver(_item("acme/api")),
    )

    assert report.rows == ()
    assert corpus.grep_calls == [(("main",), "first")]
    assert corpus.exists_calls == []


def test_any_mode_evaluates_every_positive_predicate() -> None:
    corpus = _Corpus()
    corpus.grep_map[("acme/api", "main", "second")] = (GrepHit("a", 1, "second"),)

    report = _sweep(
        corpus,
        SweepQuery(greps=("first", "second"), not_greps=("third",), any_mode=True),
        resolver=_Resolver(_item("acme/api")),
    )

    assert report.rows[0].hits == {"grep:first": 0, "grep:second": 1, "not-grep:third": 0}
    assert corpus.exists_calls == [("acme/api", "main", "third")]


def test_negative_patterns_only_ask_whether_anything_matches() -> None:
    corpus = _Corpus()
    corpus.grep_map[("acme/api", "main", "legacy")] = (
        GrepHit("a", 1, "legacy"),
        GrepHit("b", 2, "legacy"),
    )

    report = _sweep(
        corpus,
        SweepQuery(not_greps=("legacy",)),
        resolver=_Resolver(_item("acme/api"), _item("acme/new")),
    )

    assert corpus.grep_calls == []
    assert corpus.exists_calls == [("acme/api", "main", "legacy"), ("acme/new", "main", "legacy")]
    assert _names(report) == ["acme/new"]


def test_piped_records_skip_the_per_repo_lookup() -> None:
    resolver = _Resolver()

    report = _sweep(
        _readme(_Corpus(), "acme/api"),
        resolver=resolver,
        scope=RepositoryInventoryScope(),
        stdin_items=(_item("acme/api"),),
    )

    assert resolver.scopes == []
    assert _names(report) == ["acme/api"]


class _Progress:
    def __init__(self) -> None:
        self.updates: list[tuple[str, float | None, bool]] = []

    def update(
        self, message: str, *, fraction: float | None = None, new_phase: bool = False
    ) -> None:
        self.updates.append((message, fraction, new_phase))

    def log(self, line: str) -> None:
        return None


def test_progress_reports_phases_and_per_repo_counts() -> None:
    progress = _Progress()

    _sweep(
        _Corpus(sync_errors={"acme/bad": GitCorpusError("fetch denied")}),
        resolver=_Resolver(_item("acme/api"), _item("acme/bad")),
        progress=progress,
    )

    assert [message for message, _, phase in progress.updates if phase] == [
        "Resolving repositories…",
        "Sweeping 2 repos…",
    ]
    assert progress.updates[-1] == ("Sweeping 2/2 repos (1 fetched, 1 failed)", 1.0, False)


def test_sync_corpus_reports_one_outcome_per_repo() -> None:
    corpus = _Corpus(
        freshness={
            "acme/fresh": CorpusFreshness(fetched_at=datetime.now(UTC), profile="default"),
            "acme/same": _old_copy(),
        },
        sync_errors={"acme/bad": GitCorpusError("fetch denied")},
    )
    names = ("acme/bad", "acme/fresh", "acme/new", "acme/same")
    resolver = _Resolver(
        *(_item(name, pushed_at=_PUSHED) for name in names), missing=frozenset({"acme/gone"})
    )

    outcomes = SyncCorpus(
        inventory=resolver, corpus=corpus, root=Path("/corpus"), auth_header=lambda: None
    )(
        CorpusSyncOptions(
            scope=RepositoryInventoryScope(orgs=("acme",), repos=("acme/gone",)),
            stdin_repos=(),
            include_archived=False,
            refs=RefSelector(),
            refresh=False,
            max_age_seconds=3600,
            depth=1,
            parallel=1,
        )
    )

    assert [(outcome.repo, outcome.action) for outcome in outcomes] == [
        ("acme/bad", "failed"),
        ("acme/fresh", "skipped"),
        ("acme/gone", "failed"),
        ("acme/new", "synced"),
        ("acme/same", "unchanged"),
    ]
    assert outcomes[0].error == "fetch denied"
    assert next(iter(outcomes[0].model_dump())) == "repo"
