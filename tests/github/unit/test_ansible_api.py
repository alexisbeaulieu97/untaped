"""Github-owned ansible-facing API surface (Wave 2 prerequisite amendment 1).

Proves the closed surface of ``untaped.capabilities.github.ansible``:
every name the ansible capability may import, pinned identical to its
canonical implementation, with the client-operation, inventory,
settings, and result/error contracts ansible relies on exercised
through fakes — no ansible code, no network.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator
from typing import Any

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
    normalize_team_scopes,
)
from untaped.errors import UntapedError

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


class _StubInventoryService:
    """Minimal ``GithubRepositoryInventoryService`` double."""

    def get_repository(self, owner: str, repo: str) -> dict[str, Any]:
        return {
            "full_name": f"{owner}/{repo}",
            "clone_url": f"https://github.com/{owner}/{repo}.git",
            "default_branch": "main",
        }

    def list_org_repos(self, org: str) -> Iterator[dict[str, Any]]:
        yield {"full_name": f"{org}/a", "default_branch": "main"}
        yield {"full_name": f"{org}/b", "default_branch": "dev"}

    def list_team_repos(self, org: str, team_slug: str) -> Iterator[dict[str, Any]]:
        assert team_slug == "core"
        yield {"full_name": f"{org}/b", "default_branch": "dev"}
        yield {"full_name": f"{org}/c", "default_branch": "main"}


def test_inventory_expansion_contract() -> None:
    scope = RepositoryInventoryScope(
        orgs=("acme",),
        teams=normalize_team_scopes(["core"], orgs=("acme",)),
        repos=("acme/explicit",),
    )
    items = ResolveRepositoryInventory(_StubInventoryService())(scope)
    assert [item.full_name for item in items] == [
        "acme/a",
        "acme/b",
        "acme/c",
        "acme/explicit",
    ]
    row = items[0].model_dump()
    assert row["full_name"] == "acme/a"
    assert row["default_branch"] == "main"


def test_team_scope_normalization_contract() -> None:
    assert normalize_team_scopes(["core"], orgs=("acme",)) == (TeamScope(org="acme", slug="core"),)
    assert normalize_team_scopes(["acme/core"]) == (TeamScope(org="acme", slug="core"),)


def test_graphql_error_kind_contract() -> None:
    exc = GithubGraphqlError("limited", kind="rate_limited")
    assert isinstance(exc, UntapedError)
    assert exc.kind == "rate_limited"


def test_batch_refs_result_shape() -> None:
    result = BatchRepoRefsResult(
        repos=(
            RepoRefs(
                full_name="acme/a",
                default_branch="main",
                refs=(RepoRef(kind="heads", name="main", sha="abc123"),),
            ),
        ),
        missing=("acme/gone",),
        failures=(BatchRepoRefsFailure(full_name="acme/flaky", reason="boom", kind="transport"),),
        rate_limit_cost=2,
        rate_limit_remaining=10,
    )
    (repo_refs,) = result.repos
    assert repo_refs.full_name == "acme/a"
    assert [(ref.kind, ref.name, ref.sha) for ref in repo_refs.refs] == [
        ("heads", "main", "abc123")
    ]
    assert result.missing == ("acme/gone",)
    assert result.failures[0].full_name == "acme/flaky"
    assert result.rate_limit_remaining == 10
    assert result.rate_limit_cost == 2
    assert result.rate_limit_reset_at is None


def test_settings_contract() -> None:
    assert set(GithubSettings.model_fields) == {"base_url", "token", "corpus_path", "sweep"}
    assert GithubSettings().token is None
