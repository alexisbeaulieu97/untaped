"""GitHub's declared public module: the only github code other plugins import.

Ansible uses it for repository inventory, client operations, reference
probing, settings, and result/error types. Workspace never imports github:
github fills its ``RepoSource`` contract (``providers/workspace.py``). The
closed :data:`__all__` keeps the boundary explicit; everything else under
``untaped_github`` is private to GitHub.
"""

from __future__ import annotations

from untaped.sdk import get_config_section
from untaped_github.application.inventory import (
    RepositoryInventoryScope,
    ResolveRepositoryInventory,
)
from untaped_github.application.scopes import TeamScope, normalize_team_scopes
from untaped_github.domain.errors import is_global_github_failure
from untaped_github.domain.hosts import github_web_host
from untaped_github.domain.models import (
    BatchRepoRefsFailure,
    BatchRepoRefsResult,
    RepoRef,
    RepoRefs,
)
from untaped_github.errors import GithubGraphqlError, GithubGraphqlErrorKind
from untaped_github.infrastructure.github_client import GithubClient
from untaped_github.settings import GithubSettings

__all__ = [
    "BatchRepoRefsFailure",
    "BatchRepoRefsResult",
    "GithubClient",
    "GithubGraphqlError",
    "GithubGraphqlErrorKind",
    "GithubSettings",
    "RepoRef",
    "RepoRefs",
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
    so the GitHub plugin stays the only reader of its section.
    """
    return get_config_section("github", GithubSettings)
