"""Unit tests for the reusable GitHub REST client surface."""

from __future__ import annotations

import httpx
import pytest
import respx
from pydantic import SecretStr

from untaped.capabilities.github.infrastructure import GithubClient
from untaped.capabilities.github.settings import GithubSettings


def _client() -> GithubClient:
    return GithubClient(GithubSettings(token=SecretStr("ghp_test")))


def test_get_repository_returns_repo_metadata() -> None:
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/repos/acme/site").mock(
            return_value=httpx.Response(
                200,
                json={
                    "full_name": "acme/site",
                    "default_branch": "main",
                    "private": True,
                },
            )
        )
        with _client() as client:
            repo = client.get_repository("acme", "site")

    assert repo["full_name"] == "acme/site"
    assert repo["default_branch"] == "main"


def test_list_org_repos_paginates_visible_repositories() -> None:
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/orgs/acme/repos").mock(
            return_value=httpx.Response(200, json=[{"full_name": "acme/site"}])
        )
        with _client() as client:
            repos = list(client.list_org_repos("acme"))

    assert repos == [{"full_name": "acme/site"}]


def test_search_code_does_not_accept_sort_parameter() -> None:
    with _client() as client, pytest.raises(TypeError):
        client.search_code("TODO", sort="updated")  # type: ignore[call-arg]


def test_list_matching_refs_returns_branch_and_tag_refs() -> None:
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/repos/acme/site/git/matching-refs/heads").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"ref": "refs/heads/main", "object": {"sha": "abc", "type": "commit"}},
                    {"ref": "refs/heads/release/1", "object": {"sha": "def", "type": "commit"}},
                ],
            )
        )
        mock.get("/repos/acme/site/git/matching-refs/tags").mock(
            return_value=httpx.Response(
                200,
                json=[{"ref": "refs/tags/v1.0.0", "object": {"sha": "123", "type": "commit"}}],
            )
        )
        with _client() as client:
            branches = list(client.list_matching_refs("acme", "site", "heads"))
            tags = list(client.list_matching_refs("acme", "site", "tags"))

    assert [ref["ref"] for ref in branches] == ["refs/heads/main", "refs/heads/release/1"]
    assert [ref["ref"] for ref in tags] == ["refs/tags/v1.0.0"]


def test_get_tree_can_request_recursive_tree() -> None:
    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.get("/repos/acme/site/git/trees/main").mock(
            return_value=httpx.Response(
                200,
                json={
                    "sha": "abc",
                    "truncated": False,
                    "tree": [{"path": "roles/requirements.yml", "type": "blob"}],
                },
            )
        )
        with _client() as client:
            tree = client.get_tree("acme", "site", "main", recursive=True)

    assert route.calls[0].request.url.params["recursive"] == "1"
    assert tree["tree"][0]["path"] == "roles/requirements.yml"


def test_get_raw_content_reads_file_at_ref() -> None:
    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.get("/repos/acme/site/contents/roles/requirements.yml").mock(
            return_value=httpx.Response(200, text="- src: https://github.com/acme/base\n")
        )
        with _client() as client:
            content = client.get_raw_content(
                "acme",
                "site",
                "roles/requirements.yml",
                ref="release/1",
            )

    assert route.calls[0].request.url.params["ref"] == "release/1"
    assert route.calls[0].request.headers["accept"] == "application/vnd.github.raw"
    assert content == "- src: https://github.com/acme/base\n"
