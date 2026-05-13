"""Unit tests for the four ``Search*`` use cases."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast

import pytest
from untaped_core import ConfigError
from untaped_github.application import (
    GithubSearchService,
    GithubTeamService,
    SearchCode,
    SearchIssues,
    SearchRepos,
    SearchUsers,
)
from untaped_github.application.search import MAX_TEAM_REPO_QUALIFIERS
from untaped_github.domain import (
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

    def search_code(
        self, q: str, *, sort: str | None = None, limit: int | None = None
    ) -> Iterator[dict[str, Any]]:
        return self._record("code", q, sort=sort, limit=limit)

    def search_issues(
        self, q: str, *, sort: str | None = None, limit: int | None = None
    ) -> Iterator[dict[str, Any]]:
        return self._record("issues", q, sort=sort, limit=limit)

    def search_users(
        self, q: str, *, sort: str | None = None, limit: int | None = None
    ) -> Iterator[dict[str, Any]]:
        return self._record("users", q, sort=sort, limit=limit)


class _StubTeams:
    def __init__(self, repos: list[dict[str, Any]] | None = None) -> None:
        self._repos = repos or []
        self.calls: list[tuple[str, str]] = []

    def list_team_repos(self, org: str, team_slug: str) -> Iterator[dict[str, Any]]:
        self.calls.append((org, team_slug))
        return iter(self._repos)


def _stub(search: _StubSearch) -> GithubSearchService:
    return cast(GithubSearchService, search)


def _teams(stub: _StubTeams) -> GithubTeamService:
    return cast(GithubTeamService, stub)


# --- SearchRepos --------------------------------------------------------


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

    list(use_case(RepoSearchFilters(), org="acme", team="backend"))

    assert teams.calls == [("acme", "backend")]
    q = search.calls[0][1]
    assert "repo:acme/api" in q
    assert "repo:acme/web" in q
    # team resolution counts as scope; no @me injection
    assert "user:@me" not in q


def test_search_repos_team_without_org_raises() -> None:
    use_case = SearchRepos(_stub(_StubSearch([])), _teams(_StubTeams()))
    with pytest.raises(ConfigError):
        list(use_case(RepoSearchFilters(), team="backend"))


def test_search_repos_truncates_oversized_team_with_warning() -> None:
    big = [{"full_name": f"acme/repo{i}"} for i in range(MAX_TEAM_REPO_QUALIFIERS + 5)]
    teams = _StubTeams(big)
    search = _StubSearch([])
    warnings: list[str] = []
    use_case = SearchRepos(_stub(search), _teams(teams), warn=warnings.append)

    list(use_case(RepoSearchFilters(), org="acme", team="huge"))

    assert any("truncating" in w for w in warnings)
    q = search.calls[0][1]
    assert q.count("repo:") == MAX_TEAM_REPO_QUALIFIERS


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
