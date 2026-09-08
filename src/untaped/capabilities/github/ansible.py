"""GitHub-owned capability API consumed by the Ansible capability.

Ansible uses this module as the narrow inter-capability surface for repository
inventory, client operations, reference probing, settings, and result/error
types. The closed :data:`__all__` keeps that boundary explicit.

The exported types cover repository inventory, GitHub client operations,
reference-probe results, settings, and GitHub error classification.
"""

from __future__ import annotations

from untaped.capabilities.github.application.inventory import (
    RepositoryInventoryItem,
    RepositoryInventoryScope,
    ResolveRepositoryInventory,
)
from untaped.capabilities.github.application.scopes import TeamScope, normalize_team_scopes
from untaped.capabilities.github.domain.errors import GithubGraphqlError, GithubGraphqlErrorKind
from untaped.capabilities.github.domain.models import (
    BatchRepoRefsFailure,
    BatchRepoRefsResult,
    RepoRef,
    RepoRefs,
)
from untaped.capabilities.github.infrastructure.github_client import GithubClient
from untaped.capabilities.github.settings import GithubSettings

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
    "normalize_team_scopes",
]
