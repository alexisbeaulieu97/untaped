"""GithubClient REST reads used only by the ansible plugin (search/repos go via the CLI)."""

from __future__ import annotations

import httpx
import pytest
import respx
from pydantic import SecretStr

from untaped.sdk import ConfigError
from untaped_github.infrastructure import GithubClient
from untaped_github.settings import GithubSettings


def _client() -> GithubClient:
    return GithubClient(GithubSettings(token=SecretStr("ghp_test")))


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


def test_a_blank_base_url_fails_naming_the_setting() -> None:
    """``base_url`` has a default, so it is not marked; a blank override still fails loudly."""
    config = GithubSettings(base_url=" ", token=SecretStr("ghp_test"))
    with pytest.raises(ConfigError, match=r"^github\.base_url is blank$") as caught:
        GithubClient(config)
    assert "untaped config unset github.base_url" in (caught.value.hint or "")
    assert "https://api.github.com" in (caught.value.hint or "")
