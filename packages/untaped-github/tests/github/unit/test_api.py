"""The closed surface of github's public ``api`` module.

Proves ``untaped_github.api`` exports exactly the pinned names,
each identical to its canonical implementation, plus the client signatures
ansible calls. The rest of their behaviour is tested with the owning modules;
workspace reads github's repos through ``RepoSource`` (``test_workspace_provider``),
never through this module.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pytest

import untaped.sdk as sdk
from untaped_github import api as github_api
from untaped_github.api import (
    BatchRepoRefsFailure,
    BatchRepoRefsResult,
    GithubClient,
    GithubGraphqlError,
    GithubGraphqlErrorKind,
    GithubSettings,
    RepoRef,
    RepoRefs,
    RepositoryInventoryScope,
    ResolveRepositoryInventory,
    TeamScope,
    github_settings,
    github_web_host,
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
    "RepositoryInventoryScope",
    "ResolveRepositoryInventory",
    "TeamScope",
    "github_settings",
    "github_web_host",
    "is_global_github_failure",
    "normalize_team_scopes",
]


def test_surface_is_closed() -> None:
    assert github_api.__all__ == EXPECTED_ALL


def test_exports_are_canonical_objects() -> None:
    import untaped_github.application.inventory as inventory
    import untaped_github.application.scopes as scopes
    import untaped_github.domain.errors as errors
    import untaped_github.domain.hosts as hosts
    import untaped_github.domain.models as models
    import untaped_github.errors as error_classes
    import untaped_github.infrastructure.github_client as client
    import untaped_github.settings as settings

    assert ResolveRepositoryInventory is inventory.ResolveRepositoryInventory
    assert RepositoryInventoryScope is inventory.RepositoryInventoryScope
    assert TeamScope is scopes.TeamScope
    assert normalize_team_scopes is scopes.normalize_team_scopes
    assert GithubGraphqlError is error_classes.GithubGraphqlError
    assert GithubGraphqlErrorKind is error_classes.GithubGraphqlErrorKind
    assert is_global_github_failure is errors.is_global_github_failure
    assert github_web_host is hosts.github_web_host
    assert BatchRepoRefsResult is models.BatchRepoRefsResult
    assert BatchRepoRefsFailure is models.BatchRepoRefsFailure
    assert RepoRefs is models.RepoRefs
    assert RepoRef is models.RepoRef
    assert GithubClient is client.GithubClient
    assert GithubSettings is settings.GithubSettings


def test_github_specific_api_does_not_leak_into_sdk() -> None:
    # GitHub behavior belongs to its explicit inter-plugin interface,
    # independently of legitimate additions to the shared provider helpers.
    assert set(EXPECTED_ALL).isdisjoint(sdk.__all__)
    assert not any(hasattr(sdk, name) for name in EXPECTED_ALL)


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
