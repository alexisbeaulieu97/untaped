"""``GithubRepos``: GitHub fills workspace's ``RepoSource`` with its repository inventory.

``repos()`` lists the repositories of the inventory scope (``github.inventory.orgs``
and ``teams``, else ``github.default_org``) live: a failed listing raises, so
the contract's answer cache is the only place stale repos come from. A
provider with no scope is not ready, so workspace says which setting to set.

``to_repo`` turns a ``github.repo`` row (listed, or piped from any github
command) into a workspace repo without an API call: its URL is the row's
clone URL in ``github.git_protocol``, and a row whose URL is on another host,
or names another repo than ``full_name``, is refused.
"""

from __future__ import annotations

from untaped.sdk import UsageError, key, run_hint
from untaped.sdk.contracts import NotReady, Provider
from untaped_git.api import store_key
from untaped_github.application.inventory import (
    RepositoryInventoryScope,
    ResolveRepositoryInventory,
)
from untaped_github.application.scopes import normalize_team_scopes
from untaped_github.domain.errors import github_failures
from untaped_github.domain.hosts import github_web_host
from untaped_github.errors import GithubError
from untaped_github.infrastructure.github_client import GithubClient
from untaped_github.records import Repo
from untaped_github.settings import GithubSettings
from untaped_workspace.api import Repo as WorkspaceRepo
from untaped_workspace.api import RepoSource


class GithubRepos(RepoSource[Repo], Provider[GithubSettings]):
    """The repositories of GitHub's inventory scope, as workspace repos."""

    def ready(self) -> NotReady | None:
        unready = super().ready()
        if unready is not None:
            return unready
        try:
            inventory_scope(self.settings)
        except GithubError as exc:
            teams = isinstance(exc.__cause__, ValueError)  # a malformed team, not no scope
            return NotReady(
                "github.inventory.teams needs ORG/SLUG entries" if teams else "no inventory scope",
                key(GithubSettings).inventory.teams if teams else key(GithubSettings).default_org,
            )
        return None

    def to_repo(self, item: Repo) -> WorkspaceRepo:
        host = github_web_host(self.settings.api_url) or "github.com"
        if self.settings.git_protocol == "ssh":
            url = item.ssh_url or f"git@{host}:{item.full_name}.git"
        else:
            url = item.clone_url or f"https://{host}/{item.full_name}.git"
        if store_key(url) != store_key(f"https://{host}/{item.full_name}.git"):
            raise UsageError(
                f"github.repo {item.full_name}: its URL {url} is not {item.full_name} on {host}",
                hint="pipe rows from the github profile whose github.server.url lists this repo",
            )
        return WorkspaceRepo(
            name=item.full_name,
            url=url,
            default_branch=item.default_branch,
            description=item.description,
            archived=item.archived,
        )

    def repos(self) -> list[WorkspaceRepo]:
        settings = self.settings
        scope = inventory_scope(settings)
        with GithubClient(settings, http=self.ctx.http) as client, github_failures():
            rows = ResolveRepositoryInventory(client)(scope)
        return [self.to_repo(row) for row in rows]


def inventory_scope(settings: GithubSettings) -> RepositoryInventoryScope:
    """The orgs and teams the inventory lists; a :class:`GithubError` (config) when none is set."""
    inventory = settings.inventory
    orgs = tuple(inventory.orgs)
    team_orgs = orgs if len(orgs) == 1 or not settings.default_org else (settings.default_org,)
    try:
        teams = normalize_team_scopes(inventory.teams, orgs=team_orgs)
    except ValueError as exc:
        raise GithubError(
            "github.inventory.teams entries must be ORG/SLUG unless exactly one org is set",
            category="config",
            hint=f"write each team as ORG/SLUG, or {_set_default_org()}",
        ) from exc
    if not orgs and not teams and settings.default_org:
        orgs = (settings.default_org,)
    if not orgs and not teams:
        raise GithubError(
            "the repository inventory has no scope: set github.inventory.orgs, "
            "github.inventory.teams or github.default_org",
            category="config",
            hint=f"{_set_orgs()} or {_set_default_org()}",
        )
    return RepositoryInventoryScope(orgs=orgs, teams=teams)


def _set_default_org() -> str:
    from untaped.core.api import Set  # noqa: PLC0415 - core's commands load only here

    return run_hint(Set.spelled(key(GithubSettings).default_org, "ORG"))


def _set_orgs() -> str:
    from untaped.core.api import Set  # noqa: PLC0415 - core's commands load only here

    return run_hint(Set.spelled(key(GithubSettings).inventory.orgs, "'[\"ORG\"]'"))
