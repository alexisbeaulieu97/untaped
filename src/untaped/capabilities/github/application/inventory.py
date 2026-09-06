"""Use case: expand GitHub repository inventory scopes into metadata rows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict

from untaped.api import HttpError, UntapedError
from untaped.capabilities.github.application.ports import GithubRepositoryInventoryService
from untaped.capabilities.github.application.scopes import TeamScope


class RepositoryInventoryItem(BaseModel):
    """Repository metadata needed by sibling tools for source expansion."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    full_name: str
    name: str | None = None
    html_url: str | None = None
    clone_url: str | None = None
    ssh_url: str | None = None
    default_branch: str | None = None
    private: bool = False
    archived: bool = False
    fork: bool = False


@dataclass(frozen=True)
class RepositoryInventoryScope:
    """GitHub inventory selectors for orgs, teams, and explicit repositories."""

    orgs: tuple[str, ...] = ()
    teams: tuple[TeamScope, ...] = ()
    repos: tuple[str, ...] = ()


class ResolveRepositoryInventory:
    """Expand org/team/repo scopes into deduped, sorted repository metadata."""

    def __init__(self, service: GithubRepositoryInventoryService) -> None:
        self._service = service

    def __call__(self, scope: RepositoryInventoryScope) -> tuple[RepositoryInventoryItem, ...]:
        explicit: dict[str, RepositoryInventoryItem] = {}
        for full_name in scope.repos:
            owner, repo = _split_repo(full_name)
            try:
                item = _inventory_item(
                    self._service.get_repository(owner, repo),
                    fallback=full_name,
                )
            except (HttpError, UntapedError) as exc:
                raise UntapedError(
                    f"failed to expand repository {full_name}: {str(exc) or type(exc).__name__}"
                ) from exc
            explicit[item.full_name] = item

        rows: dict[str, RepositoryInventoryItem] = {}
        for org in scope.orgs:
            for row in self._service.list_org_repos(org):
                item = _inventory_item(row, fallback=None)
                rows.setdefault(item.full_name, item)
        for team in scope.teams:
            for row in self._service.list_team_repos(team.org, team.slug):
                item = _inventory_item(row, fallback=None)
                rows.setdefault(item.full_name, item)
        rows.update(explicit)
        return tuple(rows[name] for name in sorted(rows))


def _split_repo(value: str) -> tuple[str, str]:
    owner, sep, repo = value.partition("/")
    if not sep or not owner or not repo or "/" in repo:
        raise UntapedError(f"repository must be owner/name: {value!r}")
    return owner, repo


def _inventory_item(row: dict[str, Any], *, fallback: str | None) -> RepositoryInventoryItem:
    data = dict(row)
    full_name = data.get("full_name") or fallback
    if isinstance(full_name, str) and full_name:
        data["full_name"] = full_name
        if not data.get("name"):
            data["name"] = full_name.rsplit("/", 1)[1]
    return RepositoryInventoryItem.model_validate(data)
