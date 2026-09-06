"""GitHub-owned capability API consumed by the ansible capability.

Wave 2 prerequisite (import-plan amendment 1): the ansible capability
must consume GitHub behavior through exactly this module — never through
``untaped.capabilities.github.<submodule>`` privates, and never through
the sixteen ``untaped.capability_api`` provider helpers (those are NOT
the inter-capability interface and stay untouched).

Closed surface: :data:`__all__` pins the thirteen names below, which
cover precisely the five behavior groups ``untaped-ansible/src``
(@ ``806c4bca72280072e1a25509fbbedf14438d34bf``) consumes:

repository inventory
    :class:`ResolveRepositoryInventory`,
    :class:`RepositoryInventoryScope`, :class:`RepositoryInventoryItem`
    (``application/refresh_git_index.py:14-18,574-582`` — scope
    expansion plus ``item.model_dump()`` row shaping).
client operations
    :class:`GithubClient` — ``get_repository`` / ``list_org_repos`` /
    ``list_team_repos`` / ``list_matching_refs`` / ``get_tree`` /
    ``get_raw_content`` (``application/ports.py:149-171`` reader port,
    driven by ``infrastructure/github_index.py:133,161,167,179``) plus
    ``batch_repo_refs`` / ``batch_default_branch_refs``
    (``infrastructure/github_ref_probe.py:31-44,166-167``), built as
    ``GithubClient(github_settings, http=...)`` and used as a context
    manager (``cli/_refresh.py:92``; ``cli/graph_commands.py:335``).
ref probing
    result shaping only — the probe itself stays ansible-owned; the
    outcome contract is :class:`BatchRepoRefsResult` (``.repos`` items
    with ``.full_name`` / ``.default_branch`` / ``.refs`` of
    ``.kind`` / ``.name`` / ``.sha``, plus ``.missing``,
    ``.failures`` with ``.full_name`` / ``.reason``, and
    ``.rate_limit_remaining`` / ``.rate_limit_cost`` /
    ``.rate_limit_reset_at``; ``infrastructure/github_ref_probe.py:20,
    100-138``) with item types :class:`RepoRefs`, :class:`RepoRef`,
    :class:`BatchRepoRefsFailure`.
settings
    :class:`GithubSettings` (``cli/source_commands.py:21,331``;
    ``cli/graph_commands.py:20,269,334``; ``cli/_refresh.py:12,41,56,
    85,93-97`` — ``get_config_section("github", GithubSettings)`` plus
    ``.token`` secret access); :func:`normalize_team_scopes` and
    :class:`TeamScope` (``settings.py:9,80-82``;
    ``application/refresh_git_index.py:577``).
result/error types
    :class:`GithubGraphqlError` with ``.kind`` (ansible re-raises
    global failures and falls back only on ``kind == "rate_limited"``;
    ``infrastructure/github_ref_probe.py:9,168-171``,
    ``infrastructure/auto_ref_probe.py:8,63-65``); the kind vocabulary
    is :data:`GithubGraphqlErrorKind`.
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
