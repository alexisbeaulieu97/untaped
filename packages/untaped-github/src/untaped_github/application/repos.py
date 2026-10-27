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
from untaped_github.domain import ArchivedMode, archived_allows
from untaped_github.domain.repo_filters import compile_repo_pattern
from untaped_github.records import Repo


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
    ) -> Iterator[Repo]:
        rows = ResolveRepositoryInventory(self._repos)(
            RepositoryInventoryScope(orgs=orgs, teams=team_scopes)
        )
        matcher = _compile_matcher(filters)
        filtered = (row for row in rows if _matches(row, filters=filters, matcher=matcher))
        deduped = {row.full_name: row for row in filtered}
        yield from sorted(deduped.values(), key=lambda row: row.full_name.casefold())


def _matches(
    row: Repo,
    *,
    filters: RepoListFilters,
    matcher: Callable[[Repo], bool] | None,
) -> bool:
    if not archived_allows(filters.archived, row.archived):
        return False
    if filters.fork is not None and row.fork is not filters.fork:
        return False
    return matcher(row) if matcher is not None else True


def _compile_matcher(filters: RepoListFilters) -> Callable[[Repo], bool] | None:
    pattern = filters.pattern
    if not pattern:
        return None
    return compile_repo_pattern(pattern, regex=filters.regex)
