"""``GithubRepos``: GitHub fills the git plugin's ``RepoSource`` with its repository inventory.

``repos()`` lists the repositories of the inventory scope (``github.inventory.orgs``
and ``teams``, else ``github.default_org``) live: a failed listing raises, so
the contract's answer cache is the only place stale repos come from. The
provider needs a token to list or select, and ``repos`` alone also waits for an
inventory scope: ``ready()`` names the methods it can't answer, so the bridges
(no API call) work with neither.

``to_selector`` issues a ``github.repo_selector`` (orgs, teams, repos) as a
``git.repo_selector``, and ``select`` reads it back and lists its repos the
same way, whatever the inventory scope.

``to_repo`` turns a ``github.repo`` row (listed, or piped from any github
command) into a ``git.repo`` without an API call: its URL is the row's
clone URL in ``github.git_protocol``, and a row whose URL is on another host,
or names another repo than ``full_name``, is refused.
"""

from __future__ import annotations

from untaped.sdk import UsageError, key, kind_of, run_hint, setting_name
from untaped.sdk.contracts import NotReady, Provider
from untaped_git.api import Repo as GitRepo
from untaped_git.api import RepoSelector as GitRepoSelector
from untaped_git.api import RepoSource, store_key
from untaped_github.application.inventory import (
    RepositoryInventoryScope,
    ResolveRepositoryInventory,
)
from untaped_github.application.scopes import normalize_team_scopes
from untaped_github.domain.errors import github_failures
from untaped_github.domain.hosts import github_web_host
from untaped_github.errors import GithubError
from untaped_github.records import Repo, RepoSelector
from untaped_github.server import github_api
from untaped_github.settings import TOKEN, GithubSettings


class GithubRepos(RepoSource[Repo], Provider[GithubSettings]):
    """The repositories of GitHub's inventory scope, as git repos."""

    def ready(self) -> NotReady | None:
        unready = super().ready()
        if unready is not None:
            return unready
        if TOKEN.answer(self.settings.server) is None:
            return NotReady(f"no token; {_login()}", methods=("repos", "select"))
        try:
            inventory_scope(self.settings)
        except GithubError as exc:
            teams = isinstance(exc.__cause__, ValueError)  # a malformed team, not no scope
            return NotReady(
                "github.inventory.teams needs ORG/SLUG entries" if teams else "no inventory scope",
                key(GithubSettings).inventory.teams if teams else key(GithubSettings).default_org,
                methods=("repos",),
            )
        return None

    def to_repo(self, item: Repo) -> GitRepo:
        host = github_web_host(self.settings.api_url) or "github.com"
        if self.settings.git_protocol == "ssh":
            url = item.ssh_url or f"git@{host}:{item.full_name}.git"
        else:
            url = item.clone_url or f"https://{host}/{item.full_name}.git"
        if store_key(url) != store_key(f"https://{host}/{item.full_name}.git"):
            server = setting_name(key(GithubSettings).server.url)
            what = f"{kind_of(Repo)} {item.full_name}"
            raise UsageError(
                f"{what}: its URL {url} is not {item.full_name} on {host}",
                hint=f"pipe rows from the profile whose {server} lists this repo",
            )
        return GitRepo(
            name=item.full_name,
            url=url,
            default_branch=item.default_branch,
            description=item.description,
            archived=item.archived,
        )

    def to_selector(self, item: RepoSelector) -> GitRepoSelector:
        return GitRepoSelector()

    def repos(self) -> list[GitRepo]:
        return self._listed(inventory_scope(self.settings))

    def select(self, selector: GitRepoSelector) -> list[GitRepo]:
        own = self.own(selector, RepoSelector)
        teams = normalize_team_scopes(own.teams)
        return self._listed(
            RepositoryInventoryScope(orgs=tuple(own.orgs), teams=teams, repos=tuple(own.repos))
        )

    def _listed(self, scope: RepositoryInventoryScope) -> list[GitRepo]:
        with github_failures():
            rows = ResolveRepositoryInventory(github_api(self.settings))(scope)
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


def _login() -> str:
    from untaped.core.api import Login  # noqa: PLC0415 - core's commands load only here

    return run_hint(Login.spelled("github"))
