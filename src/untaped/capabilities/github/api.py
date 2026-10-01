"""GitHub's declared public module: the only github code other capabilities import.

Ansible uses it for repository inventory, client operations, reference
probing, settings, and result/error types; workspace uses
:func:`repo_inventory` for its repo picker. The closed :data:`__all__` keeps
the boundary explicit; everything else under ``capabilities/github`` is
private to GitHub.
"""

from __future__ import annotations

from untaped.capabilities.github.application.inventory import (
    RepositoryInventoryItem,
    RepositoryInventoryScope,
    ResolveRepositoryInventory,
)
from untaped.capabilities.github.application.scopes import TeamScope, normalize_team_scopes
from untaped.capabilities.github.domain.errors import (
    GithubGraphqlError,
    GithubGraphqlErrorKind,
    is_global_github_failure,
)
from untaped.capabilities.github.domain.hosts import github_web_host
from untaped.capabilities.github.domain.models import (
    BatchRepoRefsFailure,
    BatchRepoRefsResult,
    RepoRef,
    RepoRefs,
)
from untaped.capabilities.github.infrastructure.github_client import GithubClient
from untaped.capabilities.github.settings import GithubSettings
from untaped.capability_api import get_config_section

__all__ = [
    "BatchRepoRefsFailure",
    "BatchRepoRefsResult",
    "GithubClient",
    "GithubGraphqlError",
    "GithubGraphqlErrorKind",
    "GithubSettings",
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
]


def github_settings() -> GithubSettings:
    """Return the active profile's ``github`` settings (token, base URL, corpus).

    Ansible calls this instead of reading the ``github`` config section itself,
    so the GitHub capability stays the only reader of its section.
    """
    return get_config_section("github", GithubSettings)
