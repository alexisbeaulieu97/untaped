"""GithubClient REST reads used only by the ansible plugin (search/repos go via the CLI)."""

from __future__ import annotations

import httpx
import respx

from untaped.sdk.testing import Running
from untaped_github.infrastructure import GithubClient
from untaped_github.settings import GithubSettings


def _client(composed: Running) -> GithubClient:
    """A client built as a command builds its own; its token belongs to the composed plugin."""
    return GithubClient(GithubSettings(), http=composed.http)


def test_list_matching_refs_returns_branch_and_tag_refs(composed: Running) -> None:
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
        with _client(composed) as client:
            branches = list(client.list_matching_refs("acme", "site", "heads"))
            tags = list(client.list_matching_refs("acme", "site", "tags"))

    assert [ref["ref"] for ref in branches] == ["refs/heads/main", "refs/heads/release/1"]
    assert [ref["ref"] for ref in tags] == ["refs/tags/v1.0.0"]


def test_get_tree_can_request_recursive_tree(composed: Running) -> None:
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
        with _client(composed) as client:
            tree = client.get_tree("acme", "site", "main", recursive=True)

    assert route.calls[0].request.url.params["recursive"] == "1"
    assert tree["tree"][0]["path"] == "roles/requirements.yml"


def test_get_raw_content_reads_file_at_ref(composed: Running) -> None:
    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.get("/repos/acme/site/contents/roles/requirements.yml").mock(
            return_value=httpx.Response(200, text="- src: https://github.com/acme/base\n")
        )
        with _client(composed) as client:
            content = client.get_raw_content(
                "acme",
                "site",
                "roles/requirements.yml",
                ref="release/1",
            )

    assert route.calls[0].request.url.params["ref"] == "release/1"
    assert route.calls[0].request.headers["accept"] == "application/vnd.github.raw"
    assert content == "- src: https://github.com/acme/base\n"
