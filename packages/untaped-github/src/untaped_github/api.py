"""GitHub's declared public module: the only github code other capabilities import.

Ansible uses it for repository inventory, client operations, reference
probing, settings, and result/error types; workspace uses
:func:`repo_inventory` for its repo picker. The closed :data:`__all__` keeps
the boundary explicit; everything else under ``untaped_github`` is
private to GitHub.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta

from untaped.sdk import app_context, get_config_section
from untaped_github.application.inventory import (
    RepositoryInventoryItem,
    RepositoryInventoryScope,
    ResolveRepositoryInventory,
)
from untaped_github.application.inventory_cache import CachedRepoInventory
from untaped_github.application.scopes import TeamScope, normalize_team_scopes
from untaped_github.domain.errors import (
    GithubGraphqlError,
    GithubGraphqlErrorKind,
    github_failures,
    is_global_github_failure,
)
from untaped_github.domain.hosts import github_web_host
from untaped_github.domain.inventory import RepoInventory
from untaped_github.domain.models import (
    BatchRepoRefsFailure,
    BatchRepoRefsResult,
    RepoRef,
    RepoRefs,
)
from untaped_github.errors import GithubError
from untaped_github.infrastructure.github_client import GithubClient
from untaped_github.infrastructure.inventory_store import JsonInventoryStore
from untaped_github.settings import GithubSettings

__all__ = [
    "BatchRepoRefsFailure",
    "BatchRepoRefsResult",
    "GithubClient",
    "GithubGraphqlError",
    "GithubGraphqlErrorKind",
    "GithubSettings",
    "RepoInventory",
    "RepoRef",
    "RepoRefs",
    "RepositoryInventoryItem",
    "RepositoryInventoryScope",
    "ResolveRepositoryInventory",
    "TeamScope",
    "github_settings",
    "github_web_host",
    "is_global_github_failure",
    "normalize_team_scopes",
    "repo_inventory",
]


def github_settings() -> GithubSettings:
    """Return the active profile's ``github`` settings (token, base URL, corpus).

    Ansible calls this instead of reading the ``github`` config section itself,
    so the GitHub capability stays the only reader of its section.
    """
    return get_config_section("github", GithubSettings)


def repo_inventory(*, refresh: bool | None = None) -> RepoInventory:
    """The repositories the configured inventory scope can see, from the metadata cache.

    ``refresh=None`` fetches only when the cache is missing, stale
    (``github.inventory.max_age_seconds``) or for another scope; when that
    fetch fails, the stale repos come back with ``error`` set. ``True`` always
    fetches; ``False`` never touches the network. The scope is
    ``github.inventory.orgs`` and ``teams`` (a bare team slug belongs to the
    one inventory org, else ``github.default_org``); with neither,
    ``github.default_org``.
    """
    settings = github_settings()
    scope = _inventory_scope(settings)

    def fetch() -> tuple[RepositoryInventoryItem, ...]:
        with GithubClient(settings, http=app_context().http) as client, github_failures():
            return ResolveRepositoryInventory(client)(scope)

    cache = CachedRepoInventory(
        JsonInventoryStore(settings.inventory.path.expanduser()),
        fetch,
        scope_key=_scope_key(settings, scope),
        max_age=timedelta(seconds=settings.inventory.max_age_seconds),
    )
    return cache(refresh=refresh)


def _inventory_scope(settings: GithubSettings) -> RepositoryInventoryScope:
    inventory = settings.inventory
    orgs = tuple(inventory.orgs)
    team_orgs = orgs if len(orgs) == 1 or not settings.default_org else (settings.default_org,)
    try:
        teams = normalize_team_scopes(inventory.teams, orgs=team_orgs)
    except ValueError as exc:
        raise GithubError(
            "github.inventory.teams entries must be ORG/SLUG unless exactly one org is set",
            category="config",
            hint="write each team as ORG/SLUG, or run `untaped config set github.default_org ORG`",
        ) from exc
    if not orgs and not teams and settings.default_org:
        orgs = (settings.default_org,)
    if not orgs and not teams:
        raise GithubError(
            "the repository inventory has no scope: set github.inventory.orgs, "
            "github.inventory.teams or github.default_org",
            category="config",
            hint="run `untaped config set github.inventory.orgs '[\"ORG\"]'` "
            "or `untaped config set github.default_org ORG`",
        )
    return RepositoryInventoryScope(orgs=orgs, teams=teams)


def _scope_key(settings: GithubSettings, scope: RepositoryInventoryScope) -> str:
    teams = ",".join(sorted(f"{team.org}/{team.slug}" for team in scope.teams))
    token = settings.token.get_secret_value() if settings.token else ""
    # A short irreversible fingerprint: profiles with other tokens may see other repos.
    identity = hashlib.sha256(token.encode()).hexdigest()[:16] if token else "anonymous"
    return f"{settings.base_url}|token={identity}|orgs={','.join(sorted(scope.orgs))}|teams={teams}"
