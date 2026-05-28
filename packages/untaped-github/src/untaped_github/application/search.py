"""Use cases: search GitHub for repos, code, issues, and users."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from itertools import islice
from typing import Any

from pydantic import BaseModel

from untaped import ConfigError
from untaped_github.application.ports import GithubSearchService, GithubTeamService
from untaped_github.domain import (
    CodeResult,
    CodeSearchFilters,
    IssueResult,
    IssueSearchFilters,
    RepoResult,
    RepoSearchFilters,
    ScopedQueryBase,
    UserResult,
    UserSearchFilters,
)

WarnFn = Callable[[str], None]

# 200 ``repo:owner/name`` qualifiers leave ample headroom under GitHub's
# 256-char ``q`` limit and 5-boolean-operator cap. Bumping past ~250
# without measuring will start tripping the URL or operator cap.
MAX_TEAM_REPO_QUALIFIERS = 200


def _noop(_: str) -> None:
    pass


def _resolve_team_repos(
    teams: GithubTeamService,
    *,
    org: str | None,
    team: str | None,
    warn: WarnFn,
) -> tuple[str, ...]:
    """Pre-resolve ``--team`` into a tuple of ``owner/name`` strings.

    Bounded at ``MAX_TEAM_REPO_QUALIFIERS + 1`` so a 5k-repo team doesn't
    drag every page over the wire just to be truncated.
    """
    if team is None:
        return ()
    if not org:
        raise ConfigError("--team requires --org (GitHub teams are scoped to an org)")
    cap = MAX_TEAM_REPO_QUALIFIERS + 1
    repos: list[str] = []
    for entry in islice(teams.list_team_repos(org, team), cap):
        full_name = entry.get("full_name")
        if isinstance(full_name, str) and full_name:
            repos.append(full_name)
    if len(repos) > MAX_TEAM_REPO_QUALIFIERS:
        warn(
            f"team {org}/{team} has more than {MAX_TEAM_REPO_QUALIFIERS} repos; "
            "truncating to stay under GitHub's query length limit"
        )
        repos = repos[:MAX_TEAM_REPO_QUALIFIERS]
    return tuple(repos)


def _apply_scope_defaults[F: ScopedQueryBase](filters: F, team_repos: tuple[str, ...]) -> F:
    """Merge team-resolved repos and inject ``user:@me`` when no scope set."""
    repos = (*filters.repos, *team_repos)
    has_scope = bool(filters.user or filters.orgs or repos)
    overrides: dict[str, object] = {"repos": repos}
    if not has_scope:
        overrides["user"] = "@me"
    return filters.model_copy(update=overrides)


_SearchMethod = Callable[..., Iterator[dict[str, Any]]]


def _run_scoped_search[F: ScopedQueryBase, R: BaseModel](
    search_method: _SearchMethod,
    result_cls: type[R],
    teams: GithubTeamService,
    filters: F,
    *,
    org: str | None,
    team: str | None,
    warn: WarnFn,
) -> Iterator[R]:
    team_repos = _resolve_team_repos(teams, org=org, team=team, warn=warn)
    effective = _apply_scope_defaults(filters, team_repos)
    q = effective.to_query_string()
    for row in search_method(q, sort=effective.sort, limit=effective.limit):
        yield result_cls.model_validate(row)


class SearchRepos:
    """Run ``GET /search/repositories`` with scope-aware defaults."""

    def __init__(
        self,
        search: GithubSearchService,
        teams: GithubTeamService,
        *,
        warn: WarnFn = _noop,
    ) -> None:
        self._search = search
        self._teams = teams
        self._warn = warn

    def __call__(
        self,
        filters: RepoSearchFilters,
        *,
        org: str | None = None,
        team: str | None = None,
    ) -> Iterator[RepoResult]:
        return _run_scoped_search(
            self._search.search_repositories,
            RepoResult,
            self._teams,
            filters,
            org=org,
            team=team,
            warn=self._warn,
        )


class SearchCode:
    """Run ``GET /search/code`` with scope-aware defaults."""

    def __init__(
        self,
        search: GithubSearchService,
        teams: GithubTeamService,
        *,
        warn: WarnFn = _noop,
    ) -> None:
        self._search = search
        self._teams = teams
        self._warn = warn

    def __call__(
        self,
        filters: CodeSearchFilters,
        *,
        org: str | None = None,
        team: str | None = None,
    ) -> Iterator[CodeResult]:
        return _run_scoped_search(
            self._search.search_code,
            CodeResult,
            self._teams,
            filters,
            org=org,
            team=team,
            warn=self._warn,
        )


class SearchIssues:
    """Run ``GET /search/issues`` with scope-aware defaults."""

    def __init__(
        self,
        search: GithubSearchService,
        teams: GithubTeamService,
        *,
        warn: WarnFn = _noop,
    ) -> None:
        self._search = search
        self._teams = teams
        self._warn = warn

    def __call__(
        self,
        filters: IssueSearchFilters,
        *,
        org: str | None = None,
        team: str | None = None,
    ) -> Iterator[IssueResult]:
        return _run_scoped_search(
            self._search.search_issues,
            IssueResult,
            self._teams,
            filters,
            org=org,
            team=team,
            warn=self._warn,
        )


class SearchUsers:
    """Run ``GET /search/users``.

    GitHub's user-search endpoint ignores ``user:`` / ``repo:`` /
    ``org:`` qualifiers, so this use case does not resolve teams or
    inject ``user:@me`` — the only search that returns global results
    by default.
    """

    def __init__(self, search: GithubSearchService) -> None:
        self._search = search

    def __call__(self, filters: UserSearchFilters) -> Iterator[UserResult]:
        q = filters.to_query_string()
        for row in self._search.search_users(q, sort=filters.sort, limit=filters.limit):
            yield UserResult.model_validate(row)
