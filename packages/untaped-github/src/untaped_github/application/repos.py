"""Use cases: list GitHub repositories from org and team inventory scopes."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass

from untaped_github.application.inventory import (
    RepositoryInventoryScope,
    ResolveRepositoryInventory,
)
from untaped_github.application.ports import GithubRepositoryInventoryService
from untaped_github.application.scopes import TeamScope
from untaped_github.domain import ArchivedMode, GithubRepo, archived_allows
from untaped_github.domain.repo_filters import compile_repo_pattern


@dataclass(frozen=True)
class RepoListFilters:
    """Local filters for repository inventory rows."""

    pattern: str | None = None
    regex: bool = False
    # Neutral (no filter) by default; the CLI applies the user-facing "exclude".
    archived: ArchivedMode = "include"
    fork: bool | None = None


class ListRepos:
    """List repository inventory from org/team scopes."""

    def __init__(self, repos: GithubRepositoryInventoryService) -> None:
        self._repos = repos

    def __call__(
        self,
        filters: RepoListFilters,
        *,
        orgs: tuple[str, ...] = (),
        team_scopes: tuple[TeamScope, ...] = (),
    ) -> Iterator[GithubRepo]:
        rows = ResolveRepositoryInventory(self._repos)(
            RepositoryInventoryScope(orgs=orgs, teams=team_scopes)
        )
        matcher = _compile_matcher(filters)
        filtered = (row for row in rows if _matches(row, filters=filters, matcher=matcher))
        deduped = {row.full_name: row for row in filtered}
        yield from sorted(deduped.values(), key=lambda row: row.full_name.casefold())


def _matches(
    row: GithubRepo,
    *,
    filters: RepoListFilters,
    matcher: Callable[[GithubRepo], bool] | None,
) -> bool:
    if not archived_allows(filters.archived, row.archived):
        return False
    if filters.fork is not None and row.fork is not filters.fork:
        return False
    return matcher(row) if matcher is not None else True


def _compile_matcher(filters: RepoListFilters) -> Callable[[GithubRepo], bool] | None:
    pattern = filters.pattern
    if not pattern:
        return None
    return compile_repo_pattern(pattern, regex=filters.regex)
