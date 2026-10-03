"""The closed surface of github's public ``api`` module.

Proves ``untaped_github.api`` exports exactly the pinned names,
each identical to its canonical implementation, plus the client signatures
ansible calls, and the behaviour of :func:`repo_inventory`. The rest of their
behaviour is tested with the owning modules.
"""

from __future__ import annotations

import inspect
import os
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

import untaped.sdk as sdk
from untaped.sdk import UntapedError
from untaped.settings import get_settings
from untaped_github import api as github_api
from untaped_github.api import (
    BatchRepoRefsFailure,
    BatchRepoRefsResult,
    GithubClient,
    GithubGraphqlError,
    GithubGraphqlErrorKind,
    GithubSettings,
    RepoInventory,
    RepoRef,
    RepoRefs,
    RepositoryInventoryItem,
    RepositoryInventoryScope,
    ResolveRepositoryInventory,
    TeamScope,
    github_settings,
    github_web_host,
    is_global_github_failure,
    normalize_team_scopes,
    repo_inventory,
)

EXPECTED_ALL = [
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


def test_surface_is_closed() -> None:
    assert github_api.__all__ == EXPECTED_ALL


def test_exports_are_canonical_objects() -> None:
    import untaped_github.application.inventory as inventory
    import untaped_github.application.scopes as scopes
    import untaped_github.domain.errors as errors
    import untaped_github.domain.hosts as hosts
    import untaped_github.domain.inventory as domain_inventory
    import untaped_github.domain.models as models
    import untaped_github.infrastructure.github_client as client
    import untaped_github.settings as settings

    assert ResolveRepositoryInventory is inventory.ResolveRepositoryInventory
    assert RepositoryInventoryScope is inventory.RepositoryInventoryScope
    assert RepositoryInventoryItem is inventory.RepositoryInventoryItem
    assert RepoInventory is domain_inventory.RepoInventory
    assert TeamScope is scopes.TeamScope
    assert normalize_team_scopes is scopes.normalize_team_scopes
    assert GithubGraphqlError is errors.GithubGraphqlError
    assert GithubGraphqlErrorKind is errors.GithubGraphqlErrorKind
    assert is_global_github_failure is errors.is_global_github_failure
    assert github_web_host is hosts.github_web_host
    assert BatchRepoRefsResult is models.BatchRepoRefsResult
    assert BatchRepoRefsFailure is models.BatchRepoRefsFailure
    assert RepoRefs is models.RepoRefs
    assert RepoRef is models.RepoRef
    assert GithubClient is client.GithubClient
    assert GithubSettings is settings.GithubSettings


def test_github_specific_api_does_not_leak_into_sdk() -> None:
    # GitHub behavior belongs to its explicit inter-capability interface,
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


def _configure(extra: str, *, token: str = "ghp_test") -> None:
    cfg = Path(os.environ["UNTAPED_CONFIG"])
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(f"profiles:\n  default:\n    github:\n      token: {token}\n" + extra)
    get_settings.cache_clear()


ACME = [
    {
        "full_name": "acme/api",
        "clone_url": "https://github.com/acme/api.git",
        "default_branch": "main",
    },
    {"full_name": "acme/web", "clone_url": "https://github.com/acme/web.git", "archived": True},
]


def test_repo_inventory_lists_the_configured_orgs_and_caches_them() -> None:
    _configure("      inventory:\n        orgs: [acme]\n")
    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.get("/orgs/acme/repos").mock(return_value=httpx.Response(200, json=ACME))
        first = repo_inventory()
        second = repo_inventory()
    assert [r.full_name for r in first.repos] == ["acme/api", "acme/web"]
    assert second.repos == first.repos
    assert route.call_count == 1
    assert Path("~/.untaped/github-inventory.json").expanduser().is_file()


def test_repo_inventory_falls_back_to_default_org() -> None:
    _configure("      default_org: acme\n")
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/orgs/acme/repos").mock(return_value=httpx.Response(200, json=ACME))
        assert len(repo_inventory().repos) == 2


def test_repo_inventory_without_a_scope_is_a_config_error() -> None:
    _configure("")
    with pytest.raises(UntapedError, match=r"github\.inventory\.orgs") as caught:
        repo_inventory()
    assert caught.value.category == "config"


def test_repo_inventory_refresh_false_never_calls_github() -> None:
    _configure("      inventory:\n        orgs: [acme]\n")
    with respx.mock(base_url="https://api.github.com", assert_all_called=False) as mock:
        route = mock.get("/orgs/acme/repos").mock(return_value=httpx.Response(200, json=ACME))
        assert repo_inventory(refresh=False).repos == ()
    assert route.call_count == 0


def test_repo_inventory_scope_includes_the_host() -> None:
    _configure("      inventory:\n        orgs: [acme]\n")
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/orgs/acme/repos").mock(return_value=httpx.Response(200, json=ACME))
        repo_inventory()
    _configure(
        "      base_url: https://ghe.example/api/v3\n      inventory:\n        orgs: [acme]\n"
    )
    with respx.mock(base_url="https://ghe.example/api/v3") as mock:
        route = mock.get("/orgs/acme/repos").mock(return_value=httpx.Response(200, json=ACME[:1]))
        assert [r.full_name for r in repo_inventory().repos] == ["acme/api"]
    assert route.call_count == 1


def test_repo_inventory_is_keyed_by_the_token_and_never_stores_it() -> None:
    scope = "      inventory:\n        orgs: [acme]\n"
    _configure(scope, token="ghp_first_secret")
    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.get("/orgs/acme/repos").mock(return_value=httpx.Response(200, json=ACME))
        repo_inventory()
        repo_inventory()
        assert route.call_count == 1
        _configure(scope, token="ghp_second_secret")
        repo_inventory()
        assert route.call_count == 2
    cache = Path("~/.untaped/github-inventory.json").expanduser().read_text()
    assert "ghp_first_secret" not in cache
    assert "ghp_second_secret" not in cache


def test_repo_inventory_rejected_token_is_auth_with_a_hint() -> None:
    _configure("      inventory:\n        orgs: [acme]\n")
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/orgs/acme/repos").mock(
            return_value=httpx.Response(401, json={"message": "Bad credentials"})
        )
        with pytest.raises(UntapedError) as caught:
            repo_inventory()
    assert caught.value.category == "auth"
    assert caught.value.system == "github"
    assert caught.value.hint is not None
    assert "auth set github" in caught.value.hint


def test_repo_inventory_rate_limit_is_unavailable() -> None:
    _configure("      inventory:\n        orgs: [acme]\n")
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/orgs/acme/repos").mock(
            return_value=httpx.Response(
                403,
                headers={"x-ratelimit-remaining": "0"},
                json={"message": "API rate limit exceeded for user ID 1."},
            )
        )
        with pytest.raises(UntapedError) as caught:
            repo_inventory()
    assert caught.value.category == "unavailable"
    assert caught.value.system == "github"


def test_repo_inventory_bare_team_narrows_default_org() -> None:
    _configure("      default_org: acme\n      inventory:\n        teams: [platform]\n")
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/orgs/acme/teams/platform/repos").mock(
            return_value=httpx.Response(200, json=ACME[:1])
        )
        assert [r.full_name for r in repo_inventory().repos] == ["acme/api"]


def test_repo_inventory_bare_team_resolves_against_the_single_inventory_org() -> None:
    _configure(
        "      default_org: other\n      inventory:\n        orgs: [acme]\n"
        "        teams: [platform]\n"
    )
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/orgs/acme/repos").mock(return_value=httpx.Response(200, json=ACME))
        mock.get("/orgs/acme/teams/platform/repos").mock(
            return_value=httpx.Response(200, json=ACME[:1])
        )
        assert len(repo_inventory().repos) == 2


def test_repo_inventory_without_a_scope_hints_the_fix() -> None:
    _configure("")
    with pytest.raises(UntapedError) as caught:
        repo_inventory()
    assert caught.value.hint is not None
    assert "config set github.inventory.orgs" in caught.value.hint


def test_repo_inventory_bare_team_without_an_org_hints_the_fix() -> None:
    _configure("      inventory:\n        teams: [platform]\n")
    with pytest.raises(UntapedError, match=r"ORG/SLUG") as caught:
        repo_inventory()
    assert caught.value.category == "config"
    assert caught.value.hint is not None
    assert "github.default_org" in caught.value.hint
