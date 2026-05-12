"""Use cases: search GitHub for repos, code, issues, and users."""

from __future__ import annotations

from collections.abc import Callable, Iterator

from untaped_core import ConfigError

from untaped_github.application.ports import GithubSearchService, GithubTeamService
from untaped_github.domain import (
    CodeResult,
    CodeSearchFilters,
    IssueResult,
    IssueSearchFilters,
    RepoResult,
    RepoSearchFilters,
    UserResult,
    UserSearchFilters,
)

WarnFn = Callable[[str], None]

# GitHub's search API caps the ``q`` parameter at 256 chars and rejects
# queries with more than five ``OR`` / ``AND`` / ``NOT`` operators. Each
# ``repo:owner/name`` qualifier costs ~40 chars on average, so 200
# qualifiers comfortably stays inside the URL length limit while still
# covering all but the largest teams. Above that we truncate + warn.
MAX_TEAM_REPO_QUALIFIERS = 200


def _noop(_: str) -> None:
    return None


def _resolve_team_repos(
    teams: GithubTeamService,
    *,
    org: str | None,
    team: str | None,
    warn: WarnFn,
) -> tuple[str, ...]:
    """Pre-resolve ``--team`` into a tuple of ``owner/name`` strings.

    Raises :class:`ConfigError` if ``team`` is given without ``org``.
    Returns an empty tuple when no team was requested.
    """
    if team is None:
        return ()
    if not org:
        raise ConfigError("--team requires --org (GitHub teams are scoped to an org)")
    repos: list[str] = []
    for entry in teams.list_team_repos(org, team):
        full_name = entry.get("full_name")
        if isinstance(full_name, str) and full_name:
            repos.append(full_name)
    if len(repos) > MAX_TEAM_REPO_QUALIFIERS:
        warn(
            f"team {org}/{team} has {len(repos)} repos; truncating to "
            f"{MAX_TEAM_REPO_QUALIFIERS} to stay under GitHub's query length limit"
        )
        repos = repos[:MAX_TEAM_REPO_QUALIFIERS]
    return tuple(repos)


def _apply_scope_defaults[F: (RepoSearchFilters, CodeSearchFilters, IssueSearchFilters)](
    filters: F, team_repos: tuple[str, ...]
) -> F:
    """Merge team-resolved repos and inject ``user:@me`` when no scope set."""
    repos = (*filters.repos, *team_repos)
    has_scope = bool(filters.user or filters.orgs or repos)
    overrides: dict[str, object] = {"repos": repos}
    if not has_scope:
        overrides["user"] = "@me"
    return filters.model_copy(update=overrides)


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
        team_repos = _resolve_team_repos(self._teams, org=org, team=team, warn=self._warn)
        effective = _apply_scope_defaults(filters, team_repos)
        q = effective.to_query_string()
        for row in self._search.search_repositories(q, sort=effective.sort, limit=effective.limit):
            yield RepoResult.model_validate(row)


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
        team_repos = _resolve_team_repos(self._teams, org=org, team=team, warn=self._warn)
        effective = _apply_scope_defaults(filters, team_repos)
        q = effective.to_query_string()
        for row in self._search.search_code(q, sort=effective.sort, limit=effective.limit):
            yield CodeResult.model_validate(row)


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
        team_repos = _resolve_team_repos(self._teams, org=org, team=team, warn=self._warn)
        effective = _apply_scope_defaults(filters, team_repos)
        q = effective.to_query_string()
        for row in self._search.search_issues(q, sort=effective.sort, limit=effective.limit):
            yield IssueResult.model_validate(row)


class SearchUsers:
    """Run ``GET /search/users``.

    Note: GitHub's user-search endpoint ignores ``user:`` / ``repo:`` /
    ``org:`` qualifiers, so this use case does not resolve teams or
    inject ``user:@me``. It is the only search that genuinely returns
    global results by default.
    """

    def __init__(self, search: GithubSearchService) -> None:
        self._search = search

    def __call__(self, filters: UserSearchFilters) -> Iterator[UserResult]:
        q = filters.to_query_string()
        for row in self._search.search_users(q, sort=filters.sort, limit=filters.limit):
            yield UserResult.model_validate(row)
