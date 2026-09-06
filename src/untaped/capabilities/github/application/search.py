"""Use cases: search GitHub for repos, code, issues, and users."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from itertools import islice
from typing import Any

from pydantic import BaseModel

from untaped.api import HttpStatusError, UntapedError
from untaped.capabilities.github.application.ports import GithubSearchService, GithubTeamService
from untaped.capabilities.github.application.scopes import TeamScope
from untaped.capabilities.github.domain import (
    CodeResult,
    CodeSearchFilters,
    IssueResult,
    IssueSearchFilters,
    RepoResult,
    RepoSearchFilters,
    UserResult,
    UserSearchFilters,
)
from untaped.capabilities.github.domain.queries import ScopedQueryBase

WarnFn = Callable[[str], None]

# Code and issue search still keep generated team OR groups small. Repository
# search resolves full teams and splits requests by GitHub's search validation
# limits: 256 user query characters and at most five boolean operators.
MAX_TEAM_REPO_QUALIFIERS = 6
MAX_SEARCH_QUERY_TEXT_LENGTH = 256
MAX_SEARCH_BOOLEAN_OPERATORS = 5
_REPOSITORY_SEARCH_ENDPOINT = "/search/repositories"
_SEARCH_BOOLEAN_OPERATORS = {"AND", "OR", "NOT"}
_REPO_SEARCH_QUALIFIER_KEYS = frozenset(
    {
        "archived",
        "created",
        "followers",
        "fork",
        "good-first-issues",
        "help-wanted-issues",
        "in",
        "is",
        "language",
        "license",
        "mirror",
        "org",
        "pushed",
        "repo",
        "size",
        "stars",
        "template",
        "topic",
        "topics",
        "user",
        "uses",
        "visibility",
    }
)
_GLOBAL_REPO_SORTS = {"stars", "forks", "updated"}
_HELP_WANTED_BATCH_WARNING = (
    "search repos --sort help-wanted-issues is applied per GitHub request; "
    "multi-batch team searches may return batch-order-dependent selections"
)


@dataclass(frozen=True)
class _SearchQueryToken:
    value: str
    quoted: bool


@dataclass(frozen=True)
class _RepoSearchQueryBudget:
    text_length: int
    boolean_operators: int


def _noop(_: str) -> None:
    pass


def _resolve_team_repos(
    teams: GithubTeamService,
    *,
    team_scopes: tuple[TeamScope, ...],
    warn: WarnFn,
    max_repos_per_team: int | None = MAX_TEAM_REPO_QUALIFIERS,
) -> tuple[str, ...]:
    """Pre-resolve team scopes into ``owner/name`` repo strings.

    When ``max_repos_per_team`` is set, bound expansion at ``N + 1`` so a
    5k-repo team doesn't drag every page over the wire just to be truncated.
    """
    all_repos: list[str] = []
    for scope in team_scopes:
        repos: list[str] = []
        entries = teams.list_team_repos(scope.org, scope.slug)
        if max_repos_per_team is not None:
            entries = islice(entries, max_repos_per_team + 1)
        for entry in entries:
            full_name = entry.get("full_name")
            if isinstance(full_name, str) and full_name:
                repos.append(full_name)
        if max_repos_per_team is not None and len(repos) > max_repos_per_team:
            warn(
                f"team {scope.org}/{scope.slug} has more than {max_repos_per_team} repos; "
                "truncating to stay under GitHub's search operator limit"
            )
            repos = repos[:max_repos_per_team]
        all_repos.extend(repos)
    return tuple(all_repos)


def _apply_scope_defaults[F: ScopedQueryBase](filters: F, team_repos: tuple[str, ...]) -> F:
    """Merge team-resolved repos and inject ``user:@me`` when no scope set."""
    repos = _dedupe_repos((*filters.repos, *team_repos))
    has_scope = bool(filters.user or filters.orgs or repos)
    overrides: dict[str, object] = {"repos": repos}
    if not has_scope:
        overrides["user"] = "@me"
    return filters.model_copy(update=overrides)


def _dedupe_repos(repos: tuple[str, ...]) -> tuple[str, ...]:
    """Deduplicate repository scopes while preserving first-seen order."""
    seen: set[str] = set()
    deduped: list[str] = []
    for repo in repos:
        if repo in seen:
            continue
        seen.add(repo)
        deduped.append(repo)
    return tuple(deduped)


def _repo_search_batches(filters: RepoSearchFilters) -> tuple[RepoSearchFilters, ...]:
    """Split repository search filters into GitHub-validation-safe batches."""
    _ensure_search_query_fits(filters)
    user_operators = _ensure_search_boolean_operators_fit(filters)
    repos = filters.repos
    if not repos:
        return (filters,)

    max_repos_per_batch = MAX_SEARCH_BOOLEAN_OPERATORS - user_operators + 1
    batches: list[RepoSearchFilters] = []
    for start in range(0, len(repos), max_repos_per_batch):
        chunk = repos[start : start + max_repos_per_batch]
        batches.append(filters.model_copy(update={"repos": chunk}))
    return tuple(batches)


def _ensure_search_boolean_operators_fit(filters: RepoSearchFilters) -> int:
    user_operators = _search_boolean_operator_count(filters)
    if user_operators > MAX_SEARCH_BOOLEAN_OPERATORS:
        raise UntapedError(
            "GitHub repository search has "
            f"{user_operators} boolean operators; GitHub allows at most "
            f"{MAX_SEARCH_BOOLEAN_OPERATORS}. Narrow the query or remove "
            "AND/OR/NOT operators before adding repository scopes."
        )
    return user_operators


def _search_boolean_operator_count(filters: RepoSearchFilters) -> int:
    return _repo_search_query_budget(filters).boolean_operators


def _search_query_text_length(filters: RepoSearchFilters) -> int:
    return _repo_search_query_budget(filters).text_length


def _repo_search_query_budget(filters: RepoSearchFilters) -> _RepoSearchQueryBudget:
    parts: list[str] = []
    boolean_operators = 0
    for token in _tokenize_search_query(filters.raw_query or ""):
        if not token.quoted and token.value in _SEARCH_BOOLEAN_OPERATORS:
            boolean_operators += 1
            continue
        if not token.quoted and _is_repo_search_qualifier(token.value):
            continue
        if token.value:
            parts.append(token.value)
    if filters.name:
        name = filters.name.strip()
        if name:
            parts.append(name)
    return _RepoSearchQueryBudget(
        text_length=len(" ".join(parts)),
        boolean_operators=boolean_operators,
    )


def _tokenize_search_query(raw_query: str) -> tuple[_SearchQueryToken, ...]:
    tokens: list[_SearchQueryToken] = []
    chars: list[str] = []
    quoted = False
    token_quoted = False

    for char in raw_query:
        if quoted:
            if char == '"':
                quoted = False
            else:
                chars.append(char)
            continue
        if char == '"':
            quoted = True
            token_quoted = True
            continue
        if char.isspace():
            if chars or token_quoted:
                tokens.append(_SearchQueryToken("".join(chars), token_quoted))
                chars = []
                token_quoted = False
            continue
        chars.append(char)

    if chars or token_quoted:
        tokens.append(_SearchQueryToken("".join(chars), token_quoted))
    return tuple(tokens)


def _is_repo_search_qualifier(value: str) -> bool:
    token = value[1:] if value.startswith("-") else value
    key, sep, _ = token.partition(":")
    return bool(sep and key and key.lower() in _REPO_SEARCH_QUALIFIER_KEYS)


def _ensure_search_query_fits(filters: RepoSearchFilters) -> None:
    length = _search_query_text_length(filters)
    if length > MAX_SEARCH_QUERY_TEXT_LENGTH:
        raise UntapedError(
            "GitHub repository search query text length "
            f"{length} exceeds {MAX_SEARCH_QUERY_TEXT_LENGTH}; narrow the free-text query "
            "or search with fewer literal terms."
        )


def _github_search_validation_error(
    exc: HttpStatusError, filters: RepoSearchFilters
) -> HttpStatusError:
    return HttpStatusError(
        "GitHub repository search validation failed for "
        f"{_REPOSITORY_SEARCH_ENDPOINT} "
        f"(query text length {_search_query_text_length(filters)}, "
        f"boolean operators {_search_boolean_operator_count(filters)}): {exc}",
        status_code=exc.status_code,
        url=exc.url,
        body=exc.body,
    )


def _repo_search_should_globally_sort(filters: RepoSearchFilters) -> bool:
    return filters.sort in _GLOBAL_REPO_SORTS


def _sort_repo_results(rows: list[RepoResult], sort: str | None) -> list[RepoResult]:
    if sort == "stars":
        return sorted(rows, key=lambda row: (-row.stargazers_count, row.full_name))
    if sort == "forks":
        return sorted(rows, key=lambda row: (-row.forks_count, row.full_name))
    if sort == "updated":
        by_name = sorted(rows, key=lambda row: row.full_name)
        return sorted(by_name, key=lambda row: row.updated_at or "", reverse=True)
    return rows


_SearchMethod = Callable[..., Iterator[dict[str, Any]]]


def _run_scoped_search[F: RepoSearchFilters | IssueSearchFilters, R: BaseModel](
    search_method: _SearchMethod,
    result_cls: type[R],
    teams: GithubTeamService,
    filters: F,
    *,
    team_scopes: tuple[TeamScope, ...],
    warn: WarnFn,
) -> Iterator[R]:
    team_repos = _resolve_team_repos(teams, team_scopes=team_scopes, warn=warn)
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
        team_scopes: tuple[TeamScope, ...] = (),
    ) -> Iterator[RepoResult]:
        team_repos = _resolve_team_repos(
            self._teams,
            team_scopes=team_scopes,
            warn=self._warn,
            max_repos_per_team=None,
        )
        effective = _apply_scope_defaults(filters, team_repos)
        rows: list[RepoResult] = []
        seen: set[str] = set()
        globally_sort = _repo_search_should_globally_sort(effective)
        batches = _repo_search_batches(effective)
        if effective.sort == "help-wanted-issues" and len(batches) > 1:
            self._warn(_HELP_WANTED_BATCH_WARNING)
        for batch in batches:
            q = batch.to_query_string()
            try:
                search_rows = self._search.search_repositories(
                    q,
                    sort=batch.sort,
                    limit=batch.limit,
                )
                for row in search_rows:
                    result = RepoResult.model_validate(row)
                    if result.full_name in seen:
                        continue
                    seen.add(result.full_name)
                    rows.append(result)
                    if (
                        not globally_sort
                        and effective.limit is not None
                        and len(rows) >= effective.limit
                    ):
                        break
            except HttpStatusError as exc:
                if exc.status_code == 422:
                    raise _github_search_validation_error(exc, batch) from exc
                raise
            if not globally_sort and effective.limit is not None and len(rows) >= effective.limit:
                break
        if globally_sort:
            rows = _sort_repo_results(rows, effective.sort)
        if effective.limit is not None:
            rows = rows[: effective.limit]
        return iter(rows)


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
        team_scopes: tuple[TeamScope, ...] = (),
    ) -> Iterator[CodeResult]:
        team_repos = _resolve_team_repos(self._teams, team_scopes=team_scopes, warn=self._warn)
        effective = _apply_scope_defaults(filters, team_repos)
        q = effective.to_query_string()
        for row in self._search.search_code(q, limit=effective.limit):
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
        team_scopes: tuple[TeamScope, ...] = (),
    ) -> Iterator[IssueResult]:
        return _run_scoped_search(
            self._search.search_issues,
            IssueResult,
            self._teams,
            filters,
            team_scopes=team_scopes,
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
