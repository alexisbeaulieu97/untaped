"""The closed surface of github's public ``api`` module.

Proves ``untaped_github.api`` exports exactly the pinned names,
each identical to its canonical implementation, plus the port signatures
ansible calls. The rest of their behaviour is tested with the owning modules;
workspace reads github's repos through ``RepoSource`` (``test_workspace_provider``),
never through this module.
"""

from __future__ import annotations

import inspect
from typing import Any

import httpx
import respx

import untaped.sdk as sdk
from github_test_support import Settings, installed
from untaped_github import api as module
from untaped_github.api import (
    BatchRepoRefsFailure,
    BatchRepoRefsResult,
    GithubApi,
    GithubGraphqlError,
    GithubGraphqlErrorKind,
    GithubSettings,
    RepoRef,
    RepoRefs,
    RepositoryInventoryScope,
    ResolveRepositoryInventory,
    TeamScope,
    github_api,
    github_settings,
    github_web_host,
    is_global_github_failure,
    normalize_team_scopes,
    split_repo_id,
)

EXPECTED_ALL = [
    "BatchRepoRefsFailure",
    "BatchRepoRefsResult",
    "GithubApi",
    "GithubGraphqlError",
    "GithubGraphqlErrorKind",
    "GithubSettings",
    "RepoRef",
    "RepoRefs",
    "RepositoryInventoryScope",
    "ResolveRepositoryInventory",
    "TeamScope",
    "github_api",
    "github_settings",
    "github_token_found",
    "github_web_host",
    "is_global_github_failure",
    "normalize_team_scopes",
    "split_repo_id",
]


def test_surface_is_closed() -> None:
    assert module.__all__ == EXPECTED_ALL


def test_exports_are_canonical_objects() -> None:
    import untaped_github.application.inventory as inventory
    import untaped_github.application.ports as ports
    import untaped_github.application.scopes as scopes
    import untaped_github.domain.errors as errors
    import untaped_github.domain.hosts as hosts
    import untaped_github.domain.models as models
    import untaped_github.domain.repo_id as repo_id
    import untaped_github.errors as error_classes
    import untaped_github.server as server
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
    assert split_repo_id is repo_id.split_repo_id
    assert GithubApi is ports.GithubApi
    assert github_api is server.github_api
    assert GithubSettings is settings.GithubSettings


def test_github_specific_api_does_not_leak_into_sdk() -> None:
    # GitHub behavior belongs to its explicit inter-plugin interface,
    # independently of legitimate additions to the shared provider helpers.
    assert set(EXPECTED_ALL).isdisjoint(sdk.__all__)
    assert not any(hasattr(sdk, name) for name in EXPECTED_ALL)


def _params(name: str) -> Any:
    return inspect.signature(getattr(GithubApi, name)).parameters


def test_the_port_covers_ansible_reader_operations() -> None:
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
    ):
        assert callable(getattr(GithubApi, name)), name
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
    assert list(batch) == ["self", "repos", "kinds", "chunk_size", "concurrency", "on_progress"]
    assert batch["kinds"].kind is inspect.Parameter.KEYWORD_ONLY
    default_branch = params("batch_default_branch_refs")
    assert list(default_branch) == ["self", "repos", "chunk_size", "concurrency", "on_progress"]
    assert default_branch["chunk_size"].kind is inspect.Parameter.KEYWORD_ONLY


def test_github_settings_reads_the_active_github_section(settings: Settings) -> None:
    settings["default_org"] = "acme"
    installed()

    with installed().running():
        found = github_settings()

    assert isinstance(found, GithubSettings)
    assert found.default_org == "acme"


def test_github_api_is_the_port_served_for_the_servers_version() -> None:
    with (
        installed().running(),
        respx.mock(base_url="https://api.github.com") as mock,
    ):
        route = mock.get("/user").mock(return_value=httpx.Response(200, json={"login": "octo"}))
        assert github_api(github_settings()).me() == {"login": "octo"}

    assert route.calls[0].request.headers["X-GitHub-Api-Version"] == "2022-11-28"
