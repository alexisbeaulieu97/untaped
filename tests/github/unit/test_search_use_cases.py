"""Search use cases: GitHub query limits, repo batching, and merging batch results.

Flag-to-query mapping and team/scope defaults are pinned end to end in
``test_search_cli.py``; this module covers what only shows with many repos.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast

import pytest

from untaped.capabilities.github.application import (
    GithubSearchService,
    GithubTeamService,
    SearchCode,
    SearchIssues,
    SearchRepos,
    TeamScope,
)
from untaped.capabilities.github.domain import (
    CodeSearchFilters,
    IssueSearchFilters,
    RepoSearchFilters,
)
from untaped.capability_api import HttpStatusError, UntapedError

Page = list[dict[str, Any]] | Exception


class _StubSearch:
    """Record each call; answer call ``i`` with ``pages[i]`` (cut to ``limit``) or raise it."""

    def __init__(self, pages: list[Page]) -> None:
        self._pages = pages
        self.calls: list[tuple[str, str | None, int | None]] = []

    @property
    def queries(self) -> list[str]:
        return [call[0] for call in self.calls]

    def _answer(self, q: str, sort: str | None, limit: int | None) -> Iterator[dict[str, Any]]:
        self.calls.append((q, sort, limit))
        index = len(self.calls) - 1
        page = self._pages[index] if index < len(self._pages) else []
        if isinstance(page, Exception):
            raise page
        return iter(page if limit is None else page[:limit])

    def search_repositories(
        self, q: str, *, sort: str | None = None, limit: int | None = None
    ) -> Iterator[dict[str, Any]]:
        return self._answer(q, sort, limit)

    def search_code(self, q: str, *, limit: int | None = None) -> Iterator[dict[str, Any]]:
        return self._answer(q, None, limit)

    def search_issues(
        self, q: str, *, sort: str | None = None, limit: int | None = None
    ) -> Iterator[dict[str, Any]]:
        return self._answer(q, sort, limit)


class _StubTeams:
    def __init__(self, teams: dict[tuple[str, str], list[str]]) -> None:
        self._teams = teams

    def list_team_repos(self, org: str, team_slug: str) -> Iterator[dict[str, Any]]:
        return iter({"full_name": name} for name in self._teams[(org, team_slug)])


def _search(
    cls: type[SearchRepos | SearchCode | SearchIssues],
    filters: Any,
    *,
    pages: list[Page] | None = None,
    teams: dict[tuple[str, str], list[str]] | None = None,
    warnings: list[str] | None = None,
) -> tuple[list[Any], _StubSearch]:
    search = _StubSearch(pages or [])
    use_case = cls(
        cast(GithubSearchService, search),
        cast(GithubTeamService, _StubTeams(teams or {})),
        warn=(warnings if warnings is not None else []).append,
    )
    team_scopes = tuple(TeamScope(org, slug) for org, slug in teams or {})
    return list(use_case(filters, team_scopes=team_scopes)), search


def _repos(count: int, prefix: str = "acme/r") -> list[str]:
    return [f"{prefix}{i}" for i in range(count)]


def _repo_row(full_name: str, id_: int, **fields: Any) -> dict[str, Any]:
    return {
        "id": id_,
        "name": full_name.partition("/")[2],
        "full_name": full_name,
        "html_url": f"https://github.com/{full_name}",
        **fields,
    }


def _code_row(repo: str, path: str) -> dict[str, Any]:
    return {
        "name": path,
        "path": path,
        "sha": "s",
        "html_url": f"https://github.com/{repo}/blob/main/{path}",
        "repository": {"full_name": repo},
    }


def _issue_row(id_: int, updated_at: str, **fields: Any) -> dict[str, Any]:
    return {
        "id": id_,
        "number": id_,
        "title": "t",
        "state": "open",
        "html_url": f"https://github.com/acme/r/issues/{id_}",
        "repository_url": "https://api.github.com/repos/acme/r",
        "updated_at": updated_at,
        **fields,
    }


_LONG = "Desjardins/infrasinteroutils-gha-actions-commun-intergiciel"
_USES_LONG = f"uses: {_LONG}/.github/actions/set-constants-url"


@pytest.mark.parametrize(
    ("cls", "filters", "teams", "batch_sizes", "marker"),
    [
        pytest.param(
            SearchRepos,
            RepoSearchFilters(raw_query="uses: acme/action"),
            {("acme", "t"): _repos(13)},
            [6, 6, 1],
            "uses: acme/action",
            id="repos-six-qualifiers-per-query",
        ),
        pytest.param(
            SearchRepos,
            RepoSearchFilters(raw_query=_USES_LONG, archived=True),
            {("Desjardins", f"team{i}"): _repos(7, f"{_LONG}-{i}-") for i in range(3)},
            [6, 6, 6, 3],
            "archived:true",
            id="repos-long-names-across-teams",
        ),
        pytest.param(
            SearchRepos,
            RepoSearchFilters(raw_query="alpha OR beta"),
            {("acme", "t"): _repos(7)},
            [5, 2],
            "alpha OR beta",
            id="user-operators-shrink-batches",
        ),
        pytest.param(
            SearchRepos,
            RepoSearchFilters(raw_query='"OR" "AND" "NOT"'),
            {("acme", "t"): _repos(7)},
            [6, 1],
            '"OR" "AND" "NOT"',
            id="quoted-operators-do-not-count",
        ),
        pytest.param(
            SearchCode,
            CodeSearchFilters(raw_query="foo OR bar"),
            {("acme", "a"): _repos(6, "acme/a"), ("acme", "b"): _repos(6, "acme/b")},
            [5, 5, 2],
            "foo OR bar ",
            id="code",
        ),
        pytest.param(
            SearchIssues,
            IssueSearchFilters(state="open"),
            {("acme", "t"): _repos(11)},
            [6, 5],
            "is:open",
            id="issues",
        ),
    ],
)
def test_team_repos_are_split_to_github_boolean_operator_limit(
    cls: type[SearchRepos | SearchCode | SearchIssues],
    filters: Any,
    teams: dict[tuple[str, str], list[str]],
    batch_sizes: list[int],
    marker: str,
) -> None:
    warnings: list[str] = []

    _, search = _search(cls, filters, teams=teams, warnings=warnings)

    assert [q.count("repo:") for q in search.queries] == batch_sizes
    assert all(q.count(" OR ") <= 5 and marker in q for q in search.queries)
    assert warnings == []


@pytest.mark.parametrize(
    ("cls", "filters"),
    [
        (SearchRepos, RepoSearchFilters(raw_query="a OR b OR c OR d OR e OR f OR g")),
        (SearchIssues, IssueSearchFilters(raw_query="a OR b OR c OR d OR e OR f OR g")),
    ],
)
def test_query_with_too_many_boolean_operators_fails_before_search(
    cls: type[SearchRepos | SearchIssues], filters: Any
) -> None:
    search = _StubSearch([])
    use_case = cls(cast(GithubSearchService, search), cast(GithubTeamService, _StubTeams({})))

    with pytest.raises(UntapedError, match="6 boolean operators; GitHub allows at most 5"):
        list(use_case(filters))

    assert search.calls == []


_QUALIFIERS = (
    "repo:octo/super-long-repository-name org:octo "
    "uses:octo/reusable-action/.github/workflows/build.yml -language:javascript"
)


@pytest.mark.parametrize(
    ("prefix", "counted"),
    [
        pytest.param('"OR" "AND" "NOT"', "OR AND NOT", id="quoted-operators-are-text"),
        pytest.param(
            'repo:octo/repo -language:js uses:octo/action OR "repo:literal" fix:bug',
            "repo:literal fix:bug",
            id="qualifiers-and-operators-excluded",
        ),
        pytest.param('"OR repo:literal', "OR repo:literal", id="unterminated-quote-is-literal"),
        pytest.param(_QUALIFIERS, "", id="qualifiers-only"),
    ],
)
def test_search_repos_rejects_free_text_over_256_before_search(prefix: str, counted: str) -> None:
    room = 256 - len(f"{counted} ") if counted else 256

    _search(SearchRepos, RepoSearchFilters(raw_query=f"{prefix} {'x' * room}"))
    with pytest.raises(UntapedError, match="query text length 257 exceeds 256"):
        _search(SearchRepos, RepoSearchFilters(raw_query=f"{prefix} {'x' * (room + 1)}"))


def test_search_repos_dedupes_across_batches_then_applies_limit() -> None:
    rows, search = _search(
        SearchRepos,
        RepoSearchFilters(raw_query="x" * 150, limit=3),
        pages=[
            [_repo_row("acme/api", 1), _repo_row("acme/web", 2)],
            [_repo_row("acme/api", 1), _repo_row("acme/worker", 3), _repo_row("acme/extra", 4)],
        ],
        teams={("acme", "t"): _repos(7)},
    )

    assert [row.full_name for row in rows] == ["acme/api", "acme/web", "acme/worker"]
    assert [call[2] for call in search.calls] == [3, 3]


@pytest.mark.parametrize(("repo_count", "warned"), [(7, True), (2, False)])
def test_search_repos_warns_when_help_wanted_sort_spans_batches(
    repo_count: int, warned: bool
) -> None:
    warnings: list[str] = []

    _search(
        SearchRepos,
        RepoSearchFilters(sort="help-wanted-issues"),
        teams={("acme", "t"): _repos(repo_count)},
        warnings=warnings,
    )

    assert warnings == (
        [
            "search repos --sort help-wanted-issues is applied per GitHub request; "
            "multi-batch team searches may return batch-order-dependent selections"
        ]
        if warned
        else []
    )


@pytest.mark.parametrize(
    ("sort", "field", "values", "expected"),
    [
        ("stars", "stargazers_count", (3, 3, 1, 10), ["acme/z", "acme/a", "acme/b"]),
        ("forks", "forks_count", (3, 3, 1, 10), ["acme/z", "acme/a", "acme/b"]),
        (
            "updated",
            "updated_at",
            (None, "2024-01-01T00:00:00Z", "2026-01-01T00:00:00Z", "2025-01-01T00:00:00Z"),
            ["acme/c", "acme/z", "acme/b"],
        ),
    ],
)
def test_search_repos_sorted_batches_are_globally_sorted(
    sort: str, field: str, values: tuple[Any, ...], expected: list[str]
) -> None:
    a, b, c, z = values
    rows, search = _search(
        SearchRepos,
        RepoSearchFilters(raw_query="uses: acme/action", sort=sort, limit=3),  # type: ignore[arg-type]
        pages=[
            [
                _repo_row("acme/a", 1, **{field: a}),
                _repo_row("acme/b", 2, **{field: b}),
                _repo_row("acme/c", 3, **{field: c}),
            ],
            [_repo_row("acme/z", 4, **{field: z})],
        ],
        teams={("acme", "t"): _repos(7)},
    )

    assert [row.full_name for row in rows] == expected
    assert [call[1:] for call in search.calls] == [(sort, 3), (sort, 3)]


def test_search_code_merges_batches_deduped_by_html_url_and_limited() -> None:
    rows, search = _search(
        SearchCode,
        CodeSearchFilters(raw_query="TODO", repos=tuple(_repos(9)), limit=3),
        pages=[
            [_code_row("acme/r0", "a.py"), _code_row("acme/r0", "b.py")],
            [_code_row("acme/r0", "a.py"), _code_row("acme/r7", "c.py"), _code_row("acme/r8", "d")],
        ],
    )

    assert [row.path for row in rows] == ["a.py", "b.py", "c.py"]
    assert len(search.calls) == 2


_ISSUE_PAGES: list[Page] = [
    [
        _issue_row(1, "2026-01-03", comments=5, reactions={"total_count": 0}),
        _issue_row(2, "2026-01-01", comments=1, reactions={"total_count": 9}),
    ],
    [
        _issue_row(3, "2026-01-04", comments=2, reactions={"total_count": 1}),
        _issue_row(1, "2026-01-03", comments=5, reactions={"total_count": 0}),
    ],
]


@pytest.mark.parametrize(
    ("filters", "expected", "warning"),
    [
        (IssueSearchFilters(sort="updated", limit=2), [3, 1], None),
        (IssueSearchFilters(sort="comments"), [1, 3, 2], None),
        (IssueSearchFilters(sort="reactions"), [2, 3, 1], None),
        (IssueSearchFilters(sort="interactions"), [2, 1, 3], None),
        (IssueSearchFilters(raw_query="bug sort:updated-asc", limit=2), [2, 1], None),
        (IssueSearchFilters(raw_query="sort:updated-desc"), [3, 1, 2], None),
        (IssueSearchFilters(raw_query="sort:reactions-+1-desc"), [1, 2, 3], "batch order"),
    ],
)
def test_search_issues_merges_batches_by_sort_and_dedupes_by_id(
    filters: IssueSearchFilters, expected: list[int], warning: str | None
) -> None:
    warnings: list[str] = []

    rows, _ = _search(
        SearchIssues,
        filters.model_copy(update={"repos": tuple(_repos(7))}),
        pages=_ISSUE_PAGES,
        warnings=warnings,
    )

    assert [row.id for row in rows] == expected
    assert len(warnings) == (warning is not None)
    assert warning is None or warning in warnings[0]


@pytest.mark.parametrize(
    ("cls", "filters", "repo_count", "requests", "searched"),
    [
        (SearchCode, CodeSearchFilters(raw_query="TODO"), 100, 9, 54),
        (SearchCode, CodeSearchFilters(raw_query="TODO"), 54, 9, None),
        (SearchIssues, IssueSearchFilters(), 200, 25, 150),
    ],
)
def test_large_teams_are_capped_to_the_per_minute_search_budget(
    cls: type[SearchCode | SearchIssues],
    filters: Any,
    repo_count: int,
    requests: int,
    searched: int | None,
) -> None:
    warnings: list[str] = []

    _, search = _search(cls, filters, teams={("acme", "t"): _repos(repo_count)}, warnings=warnings)

    assert len(search.calls) == requests
    if searched is None:
        assert warnings == []
    else:
        [warning] = warnings
        assert f"first {searched} of {repo_count} repositories" in warning
        assert "narrow" in warning


def _http_error(status: int, message: str) -> HttpStatusError:
    return HttpStatusError(f"HTTP {status}", status_code=status, body=f'{{"message": "{message}"}}')


@pytest.mark.parametrize("status", [403, 429])
def test_search_code_rate_limit_mid_way_returns_partial_results(status: int) -> None:
    warnings: list[str] = []

    rows, search = _search(
        SearchCode,
        CodeSearchFilters(raw_query="TODO"),
        pages=[
            [_code_row("acme/r1", "f1.py")],
            [_code_row("acme/r2", "f2.py")],
            _http_error(status, "You have exceeded a secondary rate limit."),
        ],
        teams={("acme", "t"): _repos(24)},
        warnings=warnings,
    )

    assert [row.path for row in rows] == ["f1.py", "f2.py"]
    assert len(search.calls) == 3
    [warning] = warnings
    assert "rate limit after 2 of 4 requests; returning partial results" in warning


@pytest.mark.parametrize(
    "pages",
    [
        pytest.param([_http_error(403, "secondary rate limit")], id="rate-limit-on-first-batch"),
        pytest.param(
            [[_issue_row(1, "2026-01-01")], _http_error(403, "nope")], id="permission-403"
        ),
    ],
)
def test_search_issues_failures_that_are_not_partial_still_raise(pages: list[Page]) -> None:
    with pytest.raises(HttpStatusError):
        _search(SearchIssues, IssueSearchFilters(repos=tuple(_repos(12))), pages=pages)
