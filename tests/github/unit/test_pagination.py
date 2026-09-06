"""Unit tests for the GitHub pagination helpers."""

from __future__ import annotations

import httpx
import respx

from untaped.api import HttpClient
from untaped.capabilities.github.infrastructure.pagination import paginate_list, paginate_search


def _client() -> HttpClient:
    return HttpClient(base_url="https://api.github.com", headers={})


def test_paginate_search_extracts_items_envelope() -> None:
    payload = {
        "items": [
            {"id": 1, "name": "a"},
            {"id": 2, "name": "b"},
        ]
    }
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/search/repositories").mock(return_value=httpx.Response(200, json=payload))
        with _client() as http:
            rows = list(paginate_search(http, "/search/repositories", params={"q": "x"}))
    assert [r["id"] for r in rows] == [1, 2]


def test_paginate_list_returns_raw_array() -> None:
    rows_in = [{"full_name": "a/b"}, {"full_name": "c/d"}]
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/orgs/acme/teams/be/repos").mock(return_value=httpx.Response(200, json=rows_in))
        with _client() as http:
            rows = list(paginate_list(http, "/orgs/acme/teams/be/repos"))
    assert rows == rows_in


def test_paginate_returns_when_payload_is_not_a_list() -> None:
    # A 200 with a non-list body short-circuits gracefully instead of
    # erroring — defensive against odd error envelopes.
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/x").mock(return_value=httpx.Response(200, json={"message": "weird"}))
        with _client() as http:
            assert list(paginate_list(http, "/x")) == []


def test_limit_zero_short_circuits_without_calling_server() -> None:
    with respx.mock(base_url="https://api.github.com", assert_all_called=False) as mock:
        route = mock.get("/search/repositories").mock(
            return_value=httpx.Response(200, json={"items": [{"id": 1}]})
        )
        with _client() as http:
            assert (
                list(paginate_search(http, "/search/repositories", params={"q": "x"}, limit=0))
                == []
            )
    assert route.call_count == 0


def test_first_page_per_page_shrinks_to_limit() -> None:
    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.get("/search/repositories").mock(
            return_value=httpx.Response(200, json={"items": [{"id": i} for i in range(5)]})
        )
        with _client() as http:
            list(paginate_search(http, "/search/repositories", params={"q": "x"}, limit=5))
    assert route.calls[0].request.url.params["per_page"] == "5"


def test_paginate_list_delegates_link_following_to_sdk_helper() -> None:
    link = '<https://api.github.com/x?page=2>; rel="next"'

    def _respond(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("page") == "2":
            return httpx.Response(200, json=[{"a": 2}])
        return httpx.Response(200, json=[{"a": 1}], headers={"link": link})

    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.get(url__regex=r"^https://api\.github\.com/x.*").mock(side_effect=_respond)
        with _client() as http:
            rows = list(paginate_list(http, "/x"))

    assert rows == [{"a": 1}, {"a": 2}]
    assert route.call_count == 2
