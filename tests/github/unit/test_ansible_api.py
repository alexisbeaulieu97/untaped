"""Github-owned ansible-facing API surface (Wave 2 prerequisite amendment 1).

Proves the closed surface of ``untaped.capabilities.github.ansible``:
every name the ansible capability may import, pinned identical to its
canonical implementation, plus the client signatures ansible calls.
Their behaviour is tested with the owning modules.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pytest

import untaped.capability_api as capability_api
from untaped.capabilities.github import ansible as ansible_api
from untaped.capabilities.github.ansible import (
    BatchRepoRefsFailure,
    BatchRepoRefsResult,
    GithubClient,
    GithubGraphqlError,
    GithubGraphqlErrorKind,
    GithubSettings,
    RepoRef,
    RepoRefs,
    RepositoryInventoryItem,
    RepositoryInventoryScope,
    ResolveRepositoryInventory,
    TeamScope,
    github_settings,
    is_global_github_failure,
    normalize_team_scopes,
)

EXPECTED_ALL = [
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
    "is_global_github_failure",
    "normalize_team_scopes",
]


def test_surface_is_closed() -> None:
    assert ansible_api.__all__ == EXPECTED_ALL


def test_exports_are_canonical_objects() -> None:
    import untaped.capabilities.github.application.inventory as inventory
    import untaped.capabilities.github.application.scopes as scopes
    import untaped.capabilities.github.domain.errors as errors
    import untaped.capabilities.github.domain.models as models
    import untaped.capabilities.github.infrastructure.github_client as client
    import untaped.capabilities.github.settings as settings

    assert ResolveRepositoryInventory is inventory.ResolveRepositoryInventory
    assert RepositoryInventoryScope is inventory.RepositoryInventoryScope
    assert RepositoryInventoryItem is inventory.RepositoryInventoryItem
    assert TeamScope is scopes.TeamScope
    assert normalize_team_scopes is scopes.normalize_team_scopes
    assert GithubGraphqlError is errors.GithubGraphqlError
    assert GithubGraphqlErrorKind is errors.GithubGraphqlErrorKind
    assert is_global_github_failure is errors.is_global_github_failure
    assert BatchRepoRefsResult is models.BatchRepoRefsResult
    assert BatchRepoRefsFailure is models.BatchRepoRefsFailure
    assert RepoRefs is models.RepoRefs
    assert RepoRef is models.RepoRef
    assert GithubClient is client.GithubClient
    assert GithubSettings is settings.GithubSettings


def test_github_specific_api_does_not_leak_into_capability_api() -> None:
    # GitHub behavior belongs to its explicit inter-capability interface,
    # independently of legitimate additions to the shared provider helpers.
    assert set(EXPECTED_ALL).isdisjoint(capability_api.__all__)
    assert not any(hasattr(capability_api, name) for name in EXPECTED_ALL)


def _params(name: str) -> Any:
    return inspect.signature(getattr(GithubClient, name)).parameters


def test_client_covers_ansible_reader_operations() -> None:
    params = _params  # local alias for compact assertions below
    for name in (
        "get_repository",
        "list_org_repos",
        "list_team_repos",
        "list_matching_refs",
        "get_tree",
        "get_raw_content",
        "batch_repo_refs",
        "batch_default_branch_refs",
        "close",
        "__enter__",
        "__exit__",
    ):
        assert callable(getattr(GithubClient, name)), name
    assert params("get_repository").keys() == {"self", "owner", "repo"}
    assert list(params("list_team_repos")) == ["self", "org", "team_slug"]
    assert list(params("list_matching_refs")) == ["self", "owner", "repo", "namespace"]
    tree = params("get_tree")
    assert list(tree) == ["self", "owner", "repo", "tree_sha", "recursive"]
    assert tree["recursive"].kind is inspect.Parameter.KEYWORD_ONLY
    raw = params("get_raw_content")
    assert list(raw) == ["self", "owner", "repo", "path", "ref"]
    assert raw["ref"].kind is inspect.Parameter.KEYWORD_ONLY
    batch = params("batch_repo_refs")
    assert list(batch) == ["self", "repos", "kinds", "chunk_size"]
    assert batch["kinds"].kind is inspect.Parameter.KEYWORD_ONLY
    default_branch = params("batch_default_branch_refs")
    assert list(default_branch) == ["self", "repos", "chunk_size"]
    assert default_branch["chunk_size"].kind is inspect.Parameter.KEYWORD_ONLY


def test_github_settings_reads_the_active_github_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text("profiles:\n  default:\n    github:\n      token: ghp_test\n")
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))

    settings = github_settings()

    assert isinstance(settings, GithubSettings)
    assert settings.token is not None
    assert settings.token.get_secret_value() == "ghp_test"
