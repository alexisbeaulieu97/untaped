"""Unit tests for the four ``Search*`` use cases."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast

import pytest

import untaped.capabilities.github.application.search as search_module
from untaped.api import HttpStatusError, UntapedError
from untaped.capabilities.github.application import (
    GithubSearchService,
    GithubTeamService,
    SearchCode,
    SearchIssues,
    SearchRepos,
    SearchUsers,
    TeamScope,
)
from untaped.capabilities.github.application.search import MAX_TEAM_REPO_QUALIFIERS
from untaped.capabilities.github.domain import (
    CodeSearchFilters,
    IssueSearchFilters,
    RepoSearchFilters,
    UserSearchFilters,
)


class _StubSearch:
    """Captures the (query, sort, limit) it was called with and returns canned rows."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.calls: list[tuple[str, str, str | None, int | None]] = []

    def _record(
        self, endpoint: str, q: str, *, sort: str | None, limit: int | None
    ) -> Iterator[dict[str, Any]]:
        self.calls.append((endpoint, q, sort, limit))
        return iter(self._rows)

    def search_repositories(
        self, q: str, *, sort: str | None = None, limit: int | None = None
    ) -> Iterator[dict[str, Any]]:
        return self._record("repos", q, sort=sort, limit=limit)

    def search_code(self, q: str, *, limit: int | None = None) -> Iterator[dict[str, Any]]:
        return self._record("code", q, sort=None, limit=limit)

    def search_issues(
        self, q: str, *, sort: str | None = None, limit: int | None = None
    ) -> Iterator[dict[str, Any]]:
        return self._record("issues", q, sort=sort, limit=limit)

    def search_users(
        self, q: str, *, sort: str | None = None, limit: int | None = None
    ) -> Iterator[dict[str, Any]]:
        return self._record("users", q, sort=sort, limit=limit)


class _StubTeams:
    def __init__(
        self,
        repos: list[dict[str, Any]] | None = None,
        repos_by_team: dict[tuple[str, str], list[dict[str, Any]]] | None = None,
    ) -> None:
        self._repos = repos or []
        self._repos_by_team = repos_by_team or {}
        self.calls: list[tuple[str, str]] = []

    def list_team_repos(self, org: str, team_slug: str) -> Iterator[dict[str, Any]]:
        self.calls.append((org, team_slug))
        return iter(self._repos_by_team.get((org, team_slug), self._repos))


def _stub(search: _StubSearch) -> GithubSearchService:
    return cast(GithubSearchService, search)


def _teams(stub: _StubTeams) -> GithubTeamService:
    return cast(GithubTeamService, stub)


def _repo_row(
    full_name: str,
    *,
    id_: int,
    stargazers_count: int = 0,
    forks_count: int = 0,
    updated_at: str | None = None,
) -> dict[str, Any]:
    owner, _, name = full_name.partition("/")
    return {
        "id": id_,
        "name": name or owner,
        "full_name": full_name,
        "html_url": f"https://github.com/{full_name}",
        "stargazers_count": stargazers_count,
        "forks_count": forks_count,
        "updated_at": updated_at,
    }


class _QueuedSearch(_StubSearch):
    """Returns one queued page per search call and honors the provided limit."""

    def __init__(self, rows_by_call: list[list[dict[str, Any]]]) -> None:
        super().__init__([])
        self._rows_by_call = rows_by_call

    def _record(
        self, endpoint: str, q: str, *, sort: str | None, limit: int | None
    ) -> Iterator[dict[str, Any]]:
        self.calls.append((endpoint, q, sort, limit))
        rows = self._rows_by_call[len(self.calls) - 1]
        if limit is not None:
            rows = rows[:limit]
        return iter(rows)


# --- SearchRepos --------------------------------------------------------


def test_repo_search_budget_ignores_quoted_boolean_literals() -> None:
    budget = search_module._repo_search_query_budget(
        RepoSearchFilters(raw_query='"OR" "AND" "NOT"')
    )

    assert budget.boolean_operators == 0
    assert budget.text_length == len("OR AND NOT")


def test_repo_search_budget_excludes_supported_qualifiers_only() -> None:
    budget = search_module._repo_search_query_budget(
        RepoSearchFilters(
            raw_query=(
                'repo:octo/repo -language:javascript uses:octo/action OR "repo:literal" fix:bug'
            )
        )
    )

    assert budget.boolean_operators == 1
    assert budget.text_length == len("repo:literal fix:bug")


def test_repo_search_budget_treats_unterminated_quote_as_literal() -> None:
    budget = search_module._repo_search_query_budget(
        RepoSearchFilters(raw_query='"OR repo:literal')
    )

    assert budget.boolean_operators == 0
    assert budget.text_length == len("OR repo:literal")


def test_search_repos_injects_at_me_when_no_scope() -> None:
    search = _StubSearch([{"id": 1, "name": "r", "full_name": "a/r", "html_url": "u"}])
    use_case = SearchRepos(_stub(search), _teams(_StubTeams()))

    results = list(use_case(RepoSearchFilters(language="python", limit=5)))

    assert len(results) == 1
    (endpoint, q, sort, limit) = search.calls[0]
    assert endpoint == "repos"
    assert "user:@me" in q
    assert "language:python" in q
    assert limit == 5
    assert sort is None


def test_search_repos_does_not_inject_when_user_set() -> None:
    search = _StubSearch([])
    use_case = SearchRepos(_stub(search), _teams(_StubTeams()))

    list(use_case(RepoSearchFilters(user="alice")))

    q = search.calls[0][1]
    assert "user:alice" in q
    assert "user:@me" not in q


def test_search_repos_resolves_team_into_repo_qualifiers() -> None:
    search = _StubSearch([])
    teams = _StubTeams([{"full_name": "acme/api"}, {"full_name": "acme/web"}])
    use_case = SearchRepos(_stub(search), _teams(teams))

    list(use_case(RepoSearchFilters(), team_scopes=(TeamScope("acme", "backend"),)))

    assert teams.calls == [("acme", "backend")]
    q = search.calls[0][1]
    assert q == "(repo:acme/api OR repo:acme/web)"
    # team resolution counts as scope; no @me injection
    assert "user:@me" not in q


def test_search_code_resolves_team_repos_with_or_semantics() -> None:
    search = _StubSearch([])
    teams = _StubTeams([{"full_name": "acme/api"}, {"full_name": "acme/web"}])
    use_case = SearchCode(_stub(search), _teams(teams))

    list(
        use_case(
            CodeSearchFilters(raw_query="TODO"),
            team_scopes=(TeamScope("acme", "backend"),),
        )
    )

    assert teams.calls == [("acme", "backend")]
    assert search.calls[0][1] == "TODO (repo:acme/api OR repo:acme/web)"


def test_search_issues_resolves_multiple_team_scopes() -> None:
    search = _StubSearch([])
    teams = _StubTeams(
        repos_by_team={
            ("acme", "backend"): [{"full_name": "acme/api"}],
            ("platform", "ops"): [{"full_name": "platform/deploy"}],
        }
    )
    use_case = SearchIssues(_stub(search), _teams(teams))

    list(
        use_case(
            IssueSearchFilters(state="open"),
            team_scopes=(
                TeamScope("acme", "backend"),
                TeamScope("platform", "ops"),
            ),
        )
    )

    assert teams.calls == [("acme", "backend"), ("platform", "ops")]
    q = search.calls[0][1]
    assert "(repo:acme/api OR repo:platform/deploy)" in q
    assert "is:open" in q


def test_search_repos_batches_oversized_team_without_truncating() -> None:
    big = [
        {"full_name": f"Desjardins/infrasinteroutils-gha-actions-commun-intergiciel-repo{i}"}
        for i in range(MAX_TEAM_REPO_QUALIFIERS + 5)
    ]
    teams = _StubTeams(big)
    search = _StubSearch([])
    warnings: list[str] = []
    use_case = SearchRepos(_stub(search), _teams(teams), warn=warnings.append)

    list(
        use_case(
            RepoSearchFilters(
                raw_query=(
                    "uses: "
                    "Desjardins/infrasinteroutils-gha-actions-commun-intergiciel"
                    "/.github/actions/set-constants-url"
                ),
                archived=True,
            ),
            team_scopes=(TeamScope("Desjardins", "huge"),),
        )
    )

    assert warnings == []
    queries = [call[1] for call in search.calls]
    assert len(queries) > 1
    assert sum(q.count("repo:") for q in queries) == len(big)
    assert all(q.count("repo:") <= MAX_TEAM_REPO_QUALIFIERS for q in queries)
    assert all(q.count(" OR ") <= 5 for q in queries)


def test_search_repos_batches_short_names_by_boolean_operator_limit() -> None:
    repos = [{"full_name": f"acme/r{i}"} for i in range(13)]
    teams = _StubTeams(repos)
    search = _StubSearch([])
    use_case = SearchRepos(_stub(search), _teams(teams))

    list(
        use_case(
            RepoSearchFilters(raw_query="uses: acme/action"),
            team_scopes=(TeamScope("acme", "short"),),
        )
    )

    queries = [call[1] for call in search.calls]
    assert len(queries) == 3
    assert sum(q.count("repo:") for q in queries) == len(repos)
    assert all(q.count("repo:") <= MAX_TEAM_REPO_QUALIFIERS for q in queries)
    assert all(q.count(" OR ") <= 5 for q in queries)


def test_search_repos_user_boolean_operators_reduce_repo_batch_budget() -> None:
    repos = [{"full_name": f"acme/r{i}"} for i in range(7)]
    teams = _StubTeams(repos)
    search = _StubSearch([])
    use_case = SearchRepos(_stub(search), _teams(teams))

    list(
        use_case(
            RepoSearchFilters(raw_query="alpha OR beta"),
            team_scopes=(TeamScope("acme", "ops"),),
        )
    )

    queries = [call[1] for call in search.calls]
    assert len(queries) == 2
    assert sum(q.count("repo:") for q in queries) == len(repos)
    assert all(q.count("repo:") <= 5 for q in queries)
    assert all(q.count(" OR ") <= 5 for q in queries)


def test_search_repos_quoted_boolean_terms_do_not_reduce_repo_batch_budget() -> None:
    repos = [{"full_name": f"acme/r{i}"} for i in range(7)]
    teams = _StubTeams(repos)
    search = _StubSearch([])
    use_case = SearchRepos(_stub(search), _teams(teams))

    list(
        use_case(
            RepoSearchFilters(raw_query='"OR" "AND" "NOT"'),
            team_scopes=(TeamScope("acme", "ops"),),
        )
    )

    queries = [call[1] for call in search.calls]
    assert len(queries) == 2
    assert [q.count("repo:") for q in queries] == [MAX_TEAM_REPO_QUALIFIERS, 1]


def test_search_repos_rejects_raw_query_with_too_many_boolean_operators() -> None:
    search = _StubSearch([])
    use_case = SearchRepos(_stub(search), _teams(_StubTeams()))

    with pytest.raises(UntapedError) as exc_info:
        list(use_case(RepoSearchFilters(raw_query="a OR b OR c OR d OR e OR f OR g")))

    assert "boolean operators" in str(exc_info.value)
    assert "5" in str(exc_info.value)
    assert search.calls == []


def test_search_repos_rejects_raw_query_text_over_256_before_search() -> None:
    search = _StubSearch([])
    use_case = SearchRepos(_stub(search), _teams(_StubTeams()))

    list(use_case(RepoSearchFilters(raw_query="x" * 256)))

    with pytest.raises(UntapedError) as exc_info:
        list(use_case(RepoSearchFilters(raw_query="x" * 257)))

    assert "query text length" in str(exc_info.value)
    assert "256" in str(exc_info.value)
    assert len(search.calls) == 1


def test_search_repos_raw_supported_qualifiers_do_not_count_toward_256() -> None:
    search = _StubSearch([])
    use_case = SearchRepos(_stub(search), _teams(_StubTeams()))
    qualifiers = (
        "repo:octo/super-long-repository-name "
        "org:octo "
        "uses:octo/reusable-action/.github/workflows/build.yml "
        "-language:javascript"
    )

    list(use_case(RepoSearchFilters(raw_query=f"{qualifiers} {'x' * 256}")))

    with pytest.raises(UntapedError) as exc_info:
        list(use_case(RepoSearchFilters(raw_query=f"{qualifiers} {'x' * 257}")))

    assert "query text length" in str(exc_info.value)
    assert "256" in str(exc_info.value)
    assert len(search.calls) == 1


def test_search_repos_batches_multiple_teams_by_search_constraints() -> None:
    teams = _StubTeams(
        repos_by_team={
            ("Desjardins", f"team{i}"): [
                {
                    "full_name": (
                        f"Desjardins/infrasinteroutils-gha-actions-commun-intergiciel-{i}-{j}"
                    )
                }
                for j in range(7)
            ]
            for i in range(3)
        }
    )
    search = _StubSearch([])
    use_case = SearchRepos(_stub(search), _teams(teams))

    list(
        use_case(
            RepoSearchFilters(
                raw_query=(
                    "uses: "
                    "Desjardins/infrasinteroutils-gha-actions-commun-intergiciel"
                    "/.github/actions/set-constants-url"
                ),
                archived=True,
                limit=30,
            ),
            team_scopes=tuple(TeamScope("Desjardins", f"team{i}") for i in range(3)),
        )
    )

    queries = [call[1] for call in search.calls]
    assert len(queries) > 1
    assert sum(q.count("repo:") for q in queries) == 21
    assert all(q.count("repo:") <= MAX_TEAM_REPO_QUALIFIERS for q in queries)
    assert all(q.count(" OR ") <= 5 for q in queries)
    assert all("archived:true" in q for q in queries)


def test_search_repos_dedupes_across_batches_then_applies_limit() -> None:
    repos = [{"full_name": f"acme/r{i}"} for i in range(7)]
    search = _QueuedSearch(
        [
            [_repo_row("acme/api", id_=1), _repo_row("acme/web", id_=2)],
            [
                _repo_row("acme/api", id_=1),
                _repo_row("acme/worker", id_=3),
                _repo_row("acme/extra", id_=4),
            ],
        ]
    )
    use_case = SearchRepos(_stub(search), _teams(_StubTeams(repos)))

    rows = list(
        use_case(
            RepoSearchFilters(
                raw_query="x" * 150,
                limit=3,
            ),
            team_scopes=(TeamScope("Desjardins", "huge"),),
        )
    )

    assert [row.full_name for row in rows] == ["acme/api", "acme/web", "acme/worker"]
    assert len(search.calls) == 2
    assert [call[3] for call in search.calls] == [3, 3]


def test_search_repos_warns_when_help_wanted_sort_spans_batches() -> None:
    repos = [{"full_name": f"acme/r{i}"} for i in range(7)]
    search = _StubSearch([])
    warnings: list[str] = []
    use_case = SearchRepos(_stub(search), _teams(_StubTeams(repos)), warn=warnings.append)

    list(
        use_case(
            RepoSearchFilters(sort="help-wanted-issues"),
            team_scopes=(TeamScope("acme", "ops"),),
        )
    )

    assert warnings == [
        "search repos --sort help-wanted-issues is applied per GitHub request; "
        "multi-batch team searches may return batch-order-dependent selections"
    ]
    assert len(search.calls) == 2


def test_search_repos_does_not_warn_help_wanted_sort_for_single_batch() -> None:
    repos = [{"full_name": "acme/api"}, {"full_name": "acme/web"}]
    search = _StubSearch([])
    warnings: list[str] = []
    use_case = SearchRepos(_stub(search), _teams(_StubTeams(repos)), warn=warnings.append)

    list(
        use_case(
            RepoSearchFilters(sort="help-wanted-issues"),
            team_scopes=(TeamScope("acme", "ops"),),
        )
    )

    assert warnings == []
    assert len(search.calls) == 1


@pytest.mark.parametrize(
    ("sort", "rows_by_call", "expected"),
    [
        (
            "stars",
            [
                [
                    _repo_row("acme/a", id_=1, stargazers_count=3),
                    _repo_row("acme/b", id_=2, stargazers_count=3),
                    _repo_row("acme/c", id_=3, stargazers_count=1),
                ],
                [_repo_row("acme/z", id_=4, stargazers_count=10)],
            ],
            ["acme/z", "acme/a", "acme/b"],
        ),
        (
            "forks",
            [
                [
                    _repo_row("acme/a", id_=1, forks_count=3),
                    _repo_row("acme/b", id_=2, forks_count=3),
                    _repo_row("acme/c", id_=3, forks_count=1),
                ],
                [_repo_row("acme/z", id_=4, forks_count=10)],
            ],
            ["acme/z", "acme/a", "acme/b"],
        ),
        (
            "updated",
            [
                [
                    _repo_row("acme/a", id_=1, updated_at=None),
                    _repo_row("acme/b", id_=2, updated_at="2024-01-01T00:00:00Z"),
                    _repo_row("acme/c", id_=3, updated_at="2026-01-01T00:00:00Z"),
                ],
                [_repo_row("acme/z", id_=4, updated_at="2025-01-01T00:00:00Z")],
            ],
            ["acme/c", "acme/z", "acme/b"],
        ),
    ],
)
def test_search_repos_sorted_batches_are_globally_sorted(
    sort: str,
    rows_by_call: list[list[dict[str, Any]]],
    expected: list[str],
) -> None:
    repos = [{"full_name": f"acme/r{i}"} for i in range(7)]
    search = _QueuedSearch(rows_by_call)
    use_case = SearchRepos(_stub(search), _teams(_StubTeams(repos)))

    rows = list(
        use_case(
            RepoSearchFilters(
                raw_query="uses: acme/action",
                sort=sort,  # type: ignore[arg-type]
                limit=3,
            ),
            team_scopes=(TeamScope("acme", "ops"),),
        )
    )

    assert [row.full_name for row in rows] == expected
    assert len(search.calls) == 2
    assert [call[2] for call in search.calls] == [sort, sort]
    assert [call[3] for call in search.calls] == [3, 3]


def test_search_repos_422_error_preserves_http_error_metadata() -> None:
    class FailingSearch(_StubSearch):
        def search_repositories(
            self, q: str, *, sort: str | None = None, limit: int | None = None
        ) -> Iterator[dict[str, Any]]:
            self.calls.append(("repos", q, sort, limit))
            raise HttpStatusError(
                "HTTP 422 for https://api.github.com/search/repositories?q=x",
                status_code=422,
                url="https://api.github.com/search/repositories?q=x",
                body=(
                    '{"message":"Validation Failed","errors":'
                    '[{"message":"The search query is invalid"}]}'
                ),
            )

    search = FailingSearch([])
    use_case = SearchRepos(_stub(search), _teams(_StubTeams()))

    with pytest.raises(HttpStatusError) as exc_info:
        list(use_case(RepoSearchFilters(raw_query="x", repos=("acme/api",))))

    message = str(exc_info.value)
    assert "/search/repositories" in message
    assert "query text length" in message
    assert exc_info.value.status_code == 422
    assert exc_info.value.url == "https://api.github.com/search/repositories?q=x"
    assert exc_info.value.body is not None
    assert "Validation Failed" in exc_info.value.body


def test_search_code_truncates_oversized_team_with_warning() -> None:
    big = [{"full_name": f"acme/repo{i}"} for i in range(MAX_TEAM_REPO_QUALIFIERS + 5)]
    teams = _StubTeams(big)
    search = _StubSearch([])
    warnings: list[str] = []
    use_case = SearchCode(_stub(search), _teams(teams), warn=warnings.append)

    list(
        use_case(
            CodeSearchFilters(raw_query="TODO"),
            team_scopes=(TeamScope("acme", "huge"),),
        )
    )

    assert any("truncating" in w for w in warnings)
    q = search.calls[0][1]
    assert q.count("repo:") == MAX_TEAM_REPO_QUALIFIERS
    assert q.count(" OR ") == MAX_TEAM_REPO_QUALIFIERS - 1


def test_search_issues_truncates_oversized_team_with_warning() -> None:
    big = [{"full_name": f"acme/repo{i}"} for i in range(MAX_TEAM_REPO_QUALIFIERS + 5)]
    teams = _StubTeams(big)
    search = _StubSearch([])
    warnings: list[str] = []
    use_case = SearchIssues(_stub(search), _teams(teams), warn=warnings.append)

    list(
        use_case(
            IssueSearchFilters(state="open"),
            team_scopes=(TeamScope("acme", "huge"),),
        )
    )

    assert any("truncating" in w for w in warnings)
    q = search.calls[0][1]
    assert q.count("repo:") == MAX_TEAM_REPO_QUALIFIERS
    assert q.count(" OR ") == MAX_TEAM_REPO_QUALIFIERS - 1


def test_search_repos_validates_results_into_domain_model() -> None:
    search = _StubSearch(
        [
            {
                "id": 99,
                "name": "thing",
                "full_name": "me/thing",
                "html_url": "https://x",
                "stargazers_count": 12,
                "extra_field": "ignored",
            }
        ]
    )
    use_case = SearchRepos(_stub(search), _teams(_StubTeams()))
    [row] = list(use_case(RepoSearchFilters()))
    assert row.full_name == "me/thing"
    assert row.stargazers_count == 12


# --- SearchCode ---------------------------------------------------------


def test_search_code_flattens_repository_into_repo() -> None:
    search = _StubSearch(
        [
            {
                "name": "main.py",
                "path": "src/main.py",
                "sha": "deadbeef",
                "html_url": "https://x",
                "repository": {"full_name": "me/proj"},
            }
        ]
    )
    use_case = SearchCode(_stub(search), _teams(_StubTeams()))
    [row] = list(use_case(CodeSearchFilters(raw_query="TODO")))
    assert row.repo == "me/proj"
    assert row.path == "src/main.py"


def test_search_code_does_not_send_sort_to_search_service() -> None:
    search = _StubSearch([])
    use_case = SearchCode(_stub(search), _teams(_StubTeams()))

    list(use_case(CodeSearchFilters(raw_query="TODO", limit=5)))

    assert search.calls == [("code", "TODO user:@me", None, 5)]


# --- SearchIssues -------------------------------------------------------


def test_search_issues_marks_pull_request() -> None:
    search = _StubSearch(
        [
            {
                "id": 1,
                "number": 42,
                "title": "fix",
                "state": "open",
                "html_url": "https://x",
                "repository_url": "https://api.github.com/repos/me/p",
                "user": {"login": "octocat"},
                "pull_request": {"url": "..."},
            },
            {
                "id": 2,
                "number": 7,
                "title": "bug",
                "state": "closed",
                "html_url": "https://y",
                "repository_url": "https://api.github.com/repos/me/p",
                "user": {"login": "octocat"},
            },
        ]
    )
    use_case = SearchIssues(_stub(search), _teams(_StubTeams()))
    rows = list(use_case(IssueSearchFilters(state="open")))
    assert rows[0].is_pull_request is True
    assert rows[0].user_login == "octocat"
    assert rows[1].is_pull_request is False


# --- SearchUsers --------------------------------------------------------


def test_search_users_does_not_inject_at_me() -> None:
    search = _StubSearch([{"id": 1, "login": "octocat", "type": "User", "html_url": "https://x"}])
    use_case = SearchUsers(_stub(search))
    list(use_case(UserSearchFilters(kind="user", location="montreal")))
    q = search.calls[0][1]
    assert "user:@me" not in q
    assert q == "type:user location:montreal"


def test_user_filters_reject_scope_fields() -> None:
    # The scope mixin is intentionally not on UserSearchFilters; passing
    # `user=` (or `orgs=`, `repos=`) must fail loudly, not silently drop.
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        UserSearchFilters(user="@me")  # type: ignore[call-arg]
