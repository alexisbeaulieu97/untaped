"""Use cases: search GitHub for repos, code, issues, and users."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

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
from untaped.capabilities.github.domain.errors import is_rate_limited
from untaped.capabilities.github.domain.queries import ScopedQueryBase
from untaped.capability_api import HttpStatusError, UntapedError

WarnFn = Callable[[str], None]

# Repository, code, and issue search resolve full teams and split requests by
# GitHub's search validation limits: at most five boolean operators per query
# (so at most six ORed ``repo:`` qualifiers) and, for repository search, 256
# user query characters.
MAX_SEARCH_QUERY_TEXT_LENGTH = 256
MAX_SEARCH_BOOLEAN_OPERATORS = 5
MAX_TEAM_REPO_QUALIFIERS = MAX_SEARCH_BOOLEAN_OPERATORS + 1
# GitHub allows 10 code-search and 30 other search requests per minute; stay
# under those per invocation so large teams do not trip 403/429 responses.
MAX_CODE_SEARCH_BATCHES = 9
MAX_ISSUE_SEARCH_BATCHES = 25
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


def _reaction_count(row: dict[str, Any]) -> int:
    reactions = row.get("reactions")
    if isinstance(reactions, dict):
        total = reactions.get("total_count")
        if isinstance(total, int):
            return total
    return 0


def _comment_count(row: dict[str, Any]) -> int:
    comments = row.get("comments")
    return comments if isinstance(comments, int) else 0


# GitHub sorts issue search descending by default; these rebuild that order
# when several repo batches must be merged locally.
_ISSUE_SORT_KEYS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "created": lambda row: row.get("created_at") or "",
    "updated": lambda row: row.get("updated_at") or "",
    "comments": _comment_count,
    "reactions": _reaction_count,
    "interactions": lambda row: _comment_count(row) + _reaction_count(row),
}


def _resolve_team_repos(
    teams: GithubTeamService,
    *,
    team_scopes: tuple[TeamScope, ...],
) -> tuple[str, ...]:
    """Pre-resolve team scopes into ``owner/name`` repo strings."""
    all_repos: list[str] = []
    for scope in team_scopes:
        for entry in teams.list_team_repos(scope.org, scope.slug):
            full_name = entry.get("full_name")
            if isinstance(full_name, str) and full_name:
                all_repos.append(full_name)
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
    return _scoped_search_batches(filters, kind="repository")


def _scoped_search_batches[F: ScopedQueryBase](filters: F, *, kind: str) -> tuple[F, ...]:
    """Split repo scopes so each query stays within GitHub's boolean-operator limit."""
    user_operators = _ensure_search_boolean_operators_fit(filters, kind=kind)
    repos = filters.repos
    if not repos:
        return (filters,)

    max_repos_per_batch = MAX_SEARCH_BOOLEAN_OPERATORS - user_operators + 1
    batches: list[F] = []
    for start in range(0, len(repos), max_repos_per_batch):
        chunk = repos[start : start + max_repos_per_batch]
        batches.append(filters.model_copy(update={"repos": chunk}))
    return tuple(batches)


def _ensure_search_boolean_operators_fit(filters: ScopedQueryBase, *, kind: str) -> int:
    user_operators = _search_boolean_operator_count(filters)
    if user_operators > MAX_SEARCH_BOOLEAN_OPERATORS:
        raise UntapedError(
            f"GitHub {kind} search has "
            f"{user_operators} boolean operators; GitHub allows at most "
            f"{MAX_SEARCH_BOOLEAN_OPERATORS}. Narrow the query or remove "
            "AND/OR/NOT operators before adding repository scopes."
        )
    return user_operators


def _search_boolean_operator_count(filters: ScopedQueryBase) -> int:
    return sum(
        1
        for token in _tokenize_search_query(filters.raw_query or "")
        if not token.quoted and token.value in _SEARCH_BOOLEAN_OPERATORS
    )


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


def _cap_batches[F: ScopedQueryBase](
    batches: tuple[F, ...], *, kind: str, max_batches: int, warn: WarnFn
) -> tuple[F, ...]:
    """Keep the first ``max_batches`` batches, warning which repos were searched."""
    if len(batches) <= max_batches:
        return batches
    kept = batches[:max_batches]
    searched = sum(len(batch.repos) for batch in kept)
    total = sum(len(batch.repos) for batch in batches)
    warn(
        f"GitHub {kind} search is rate limited per minute; results cover only the "
        f"first {searched} of {total} repositories ({max_batches} requests); "
        "narrow the scope with --repo or a smaller team to search the rest"
    )
    return kept


def _raw_issue_sort(raw_query: str | None) -> tuple[str, bool] | None:
    """Return the last ``sort:<field>[-asc|-desc]`` qualifier as (field, descending)."""
    found: tuple[str, bool] | None = None
    for token in _tokenize_search_query(raw_query or ""):
        if token.quoted or not token.value.lower().startswith("sort:"):
            continue
        value = token.value[len("sort:") :].lower()
        descending = True
        if value.endswith("-asc"):
            value, descending = value[: -len("-asc")], False
        elif value.endswith("-desc"):
            value = value[: -len("-desc")]
        found = (value, descending)
    return found


def _merged_batch_search[F: ScopedQueryBase, R: BaseModel](
    batches: tuple[F, ...],
    run: Callable[[F], Iterable[dict[str, Any]]],
    result_cls: type[R],
    *,
    identity: Callable[[R], object],
    limit: int | None,
    sort_key: Callable[[dict[str, Any]], Any] | None = None,
    descending: bool = True,
    kind: str = "",
    warn: WarnFn = _noop,
) -> Iterator[R]:
    """Run each batch, dedupe by ``identity``, and apply ``limit`` across batches.

    With a ``sort_key`` and more than one batch, every batch is queried and
    the merged rows are re-sorted (descending, GitHub's default order, unless
    ``descending`` is false) before the limit, so the selection does not
    depend on batch order. A rate limit after the first batch returns the
    rows merged so far with a warning instead of discarding them.
    """
    merging = len(batches) > 1
    globally_sort = sort_key is not None and merging
    rows: list[tuple[dict[str, Any], R]] = []
    seen: set[object] = set()
    for index, batch in enumerate(batches):
        try:
            for row in run(batch):
                result = result_cls.model_validate(row)
                key = identity(result)
                if merging and key in seen:
                    continue
                seen.add(key)
                rows.append((row, result))
                if not globally_sort and limit is not None and len(rows) >= limit:
                    break
        except HttpStatusError as exc:
            if index == 0 or not is_rate_limited(exc.status_code, exc.body):
                raise
            searched = sum(len(done.repos) for done in batches[:index])
            total = sum(len(each.repos) for each in batches)
            warn(
                f"GitHub {kind} search hit a rate limit after {index} of {len(batches)} "
                f"requests; returning partial results covering {searched} of {total} "
                "repositories"
            )
            break
        if not globally_sort and limit is not None and len(rows) >= limit:
            break
    if globally_sort and sort_key is not None:
        rows.sort(key=lambda pair: sort_key(pair[0]), reverse=descending)
    if limit is not None:
        rows = rows[:limit]
    return iter([result for _, result in rows])


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
        team_repos = _resolve_team_repos(self._teams, team_scopes=team_scopes)
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
        team_repos = _resolve_team_repos(self._teams, team_scopes=team_scopes)
        effective = _apply_scope_defaults(filters, team_repos)
        batches = _cap_batches(
            _scoped_search_batches(effective, kind="code"),
            kind="code",
            max_batches=MAX_CODE_SEARCH_BATCHES,
            warn=self._warn,
        )
        return _merged_batch_search(
            batches,
            lambda batch: self._search.search_code(batch.to_query_string(), limit=batch.limit),
            CodeResult,
            identity=lambda row: row.html_url,
            limit=effective.limit,
            kind="code",
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
        team_scopes: tuple[TeamScope, ...] = (),
    ) -> Iterator[IssueResult]:
        team_repos = _resolve_team_repos(self._teams, team_scopes=team_scopes)
        effective = _apply_scope_defaults(filters, team_repos)
        batches = _cap_batches(
            _scoped_search_batches(effective, kind="issue"),
            kind="issue",
            max_batches=MAX_ISSUE_SEARCH_BATCHES,
            warn=self._warn,
        )
        sort_key = _ISSUE_SORT_KEYS.get(effective.sort) if effective.sort else None
        descending = True
        raw_sort = None if effective.sort else _raw_issue_sort(effective.raw_query)
        if raw_sort is not None:
            field, descending = raw_sort
            sort_key = _ISSUE_SORT_KEYS.get(field)
            if sort_key is None and len(batches) > 1:
                self._warn(
                    f"search issues cannot merge batches by sort:{field}; "
                    "multi-batch results use batch order"
                )
        return _merged_batch_search(
            batches,
            lambda batch: self._search.search_issues(
                batch.to_query_string(), sort=batch.sort, limit=batch.limit
            ),
            IssueResult,
            identity=lambda row: row.id,
            limit=effective.limit,
            sort_key=sort_key,
            descending=descending,
            kind="issue",
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
