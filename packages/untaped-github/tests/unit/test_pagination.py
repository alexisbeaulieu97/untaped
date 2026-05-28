"""Unit tests for the GitHub pagination helpers."""

from __future__ import annotations

import httpx
import pytest
import respx
from untaped_github.infrastructure.pagination import paginate_list, paginate_search

from untaped import HttpClient, UntapedError


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


def test_self_loop_link_header_terminates() -> None:
    # A server that returns a `next` URL identical to the current URL
    # would otherwise loop forever. The cycle guard catches it on the
    # next iteration — may revisit the page once before bailing, but
    # must terminate (and far short of _MAX_PAGES).
    link = '<https://api.github.com/x>; rel="next"'
    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.get("/x").mock(
            return_value=httpx.Response(200, json=[{"a": 1}], headers={"Link": link})
        )
        with _client() as http:
            rows = list(paginate_list(http, "/x"))
    assert rows == [{"a": 1}, {"a": 1}]
    assert route.call_count == 2


def test_alternating_link_cycle_terminates() -> None:
    # `next` URLs that ping-pong (A -> B -> A) terminate after the
    # visited set catches the back-edge.
    link_a = '<https://api.github.com/x?cursor=b>; rel="next"'
    link_b = '<https://api.github.com/x>; rel="next"'
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/x", params={"cursor": "b"}).mock(
            return_value=httpx.Response(200, json=[{"a": 2}], headers={"Link": link_b})
        )
        mock.get("/x").mock(
            return_value=httpx.Response(200, json=[{"a": 1}], headers={"Link": link_a})
        )
        with _client() as http:
            rows = list(paginate_list(http, "/x"))
    # Two full pages plus one re-visit before the cycle guard fires.
    assert len(rows) <= 4
    assert {"a": 1} in rows and {"a": 2} in rows


def test_runaway_next_chain_raises_after_max_pages() -> None:
    # Every page points at a fresh unique URL — the visited set lets us
    # walk forever. The page-count cap must stop us at _MAX_PAGES (100).
    counter = 0

    def _respond(request: httpx.Request) -> httpx.Response:
        nonlocal counter
        counter += 1
        next_url = f'<https://api.github.com/x?p={counter + 1}>; rel="next"'
        return httpx.Response(200, json=[{"i": counter}], headers={"Link": next_url})

    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get(url__regex=r"^https://api\.github\.com/x.*").mock(side_effect=_respond)
        with _client() as http, pytest.raises(UntapedError, match="did not converge"):
            list(paginate_list(http, "/x"))
