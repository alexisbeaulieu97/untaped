"""``Rest2022``'s REST reads that only ansible makes (search and repos go through the CLI)."""

from __future__ import annotations

import httpx
import respx

from untaped.sdk.testing import Running
from untaped_github.application.ports import GithubApi
from untaped_github.server import SERVER_API
from untaped_github.settings import GithubSettings


def _client(composed: Running) -> GithubApi:
    """github's port as a command reaches it; its token belongs to the composed plugin."""
    del composed  # the run the port's calls are made in
    return SERVER_API.port(GithubSettings())


def test_a_search_with_no_room_asks_nothing(composed: Running) -> None:
    with respx.mock(base_url="https://api.github.com", assert_all_called=False) as mock:
        route = mock.get("/search/repositories").mock(
            return_value=httpx.Response(200, json={"items": [{"id": 1}]})
        )
        client = _client(composed)
        assert list(client.search_repositories("x", limit=0)) == []
    assert route.call_count == 0


def test_a_search_reads_the_rows_under_items(composed: Running) -> None:
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/search/code").mock(
            return_value=httpx.Response(200, json={"total_count": 1, "items": [{"id": 1}]})
        )
        client = _client(composed)
        assert list(client.search_code("x")) == [{"id": 1}]
