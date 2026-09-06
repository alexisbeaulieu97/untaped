"""Unit tests for the Jira REST client."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx
from pydantic import SecretStr

from untaped.api import ConfigError, HttpClient, HttpSettings
from untaped.capabilities.jira.infrastructure import JiraClient
from untaped.capabilities.jira.infrastructure import jira_client as jira_client_module
from untaped.capabilities.jira.settings import JiraSettings


def _settings() -> JiraSettings:
    return JiraSettings(
        base_url="https://jira.example.com",
        token=SecretStr("jira_pat"),
    )


def test_client_sends_pat_bearer_header_and_joins_api_prefix() -> None:
    with respx.mock(base_url="https://jira.example.com") as mock:
        route = mock.get("/rest/api/2/myself").mock(
            return_value=httpx.Response(200, json={"name": "alexis", "displayName": "Alexis"})
        )
        with JiraClient(_settings()) as client:
            assert client.me()["name"] == "alexis"

    assert route.calls[0].request.headers["authorization"] == "Bearer jira_pat"
    assert route.calls[0].request.headers["accept"] == "application/json"


def test_client_forwards_section_and_http_settings_to_connected_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[Any, Any]] = []
    real_connected_client = jira_client_module.connected_client

    def spying_connected_client(config: JiraSettings, **kwargs: Any) -> HttpClient:
        seen.append((kwargs.get("section"), kwargs.get("http")))
        return real_connected_client(config, **kwargs)

    monkeypatch.setattr(jira_client_module, "connected_client", spying_connected_client)

    client = JiraClient(_settings(), http=HttpSettings(verify_ssl=False))
    client.close()

    assert seen == [("jira", HttpSettings(verify_ssl=False))]


def test_client_requires_base_url() -> None:
    config = JiraSettings(token=SecretStr("jira_pat"))

    with pytest.raises(ConfigError, match=r"jira\.base_url"):
        JiraClient(config)


def test_client_requires_non_blank_token() -> None:
    config = JiraSettings(base_url="https://jira.example.com", token=SecretStr("   "))

    with pytest.raises(ConfigError, match=r"jira\.token"):
        JiraClient(config)


def test_search_issues_posts_jql_and_paginates() -> None:
    with respx.mock(base_url="https://jira.example.com") as mock:
        route = mock.post("/rest/api/2/search").mock(
            return_value=httpx.Response(
                200,
                json={
                    "startAt": 0,
                    "maxResults": 2,
                    "total": 1,
                    "issues": [{"key": "ABC-1", "fields": {"summary": "one"}}],
                },
            )
        )
        with JiraClient(_settings()) as client:
            rows = list(client.search_issues("project = ABC", limit=1))

    assert rows[0]["key"] == "ABC-1"
    request_json = json.loads(route.calls[0].request.content)
    assert request_json["jql"] == "project = ABC"
    assert request_json["maxResults"] == 1


def test_search_issues_walks_start_at_pages_until_total() -> None:
    settings = JiraSettings(
        base_url="https://jira.example.com",
        token=SecretStr("jira_pat"),
        page_size=1,
    )
    with respx.mock(base_url="https://jira.example.com") as mock:
        route = mock.post("/rest/api/2/search").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "startAt": 0,
                        "maxResults": 1,
                        "total": 2,
                        "issues": [{"key": "ABC-1", "fields": {"summary": "one"}}],
                    },
                ),
                httpx.Response(
                    200,
                    json={
                        "startAt": 1,
                        "maxResults": 1,
                        "total": 2,
                        "issues": [{"key": "ABC-2", "fields": {"summary": "two"}}],
                    },
                ),
            ]
        )
        with JiraClient(settings) as client:
            rows = list(client.search_issues("project = ABC"))

    assert [row["key"] for row in rows] == ["ABC-1", "ABC-2"]
    second_request = json.loads(route.calls[1].request.content)
    assert second_request["startAt"] == 1


def test_client_passes_explicit_sdk3_pagination_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_paginate_offset(*_args: Any, **kwargs: Any) -> Iterator[dict[str, Any]]:
        calls.append(kwargs)
        return iter(())

    monkeypatch.setattr(jira_client_module, "paginate_offset", fake_paginate_offset)

    with JiraClient(_settings()) as client:
        list(client.search_issues("project = ABC", limit=1))
        list(client.list_boards(limit=1))
        list(client.list_sprints(7, limit=1))

    assert calls[0]["start_param"] == "startAt"
    assert calls[0]["size_param"] == "maxResults"
    assert "last_flag" not in calls[0]
    assert calls[0]["retry"] is jira_client_module._SEARCH_RETRY
    assert calls[1]["start_param"] == "startAt"
    assert calls[1]["size_param"] == "maxResults"
    assert calls[1]["last_flag"] == "isLast"
    assert calls[2]["start_param"] == "startAt"
    assert calls[2]["size_param"] == "maxResults"
    assert calls[2]["last_flag"] == "isLast"


def test_list_boards_shrinks_page_request_to_limit() -> None:
    with respx.mock(base_url="https://jira.example.com") as mock:
        route = mock.get("/rest/agile/1.0/board").mock(
            return_value=httpx.Response(
                200,
                json={"startAt": 0, "maxResults": 1, "isLast": True, "values": [{"id": 1}]},
            )
        )
        with JiraClient(_settings()) as client:
            rows = list(client.list_boards(limit=1))

    assert rows == [{"id": 1}]
    assert route.calls[0].request.url.params["maxResults"] == "1"


def test_list_boards_continues_when_is_last_false_on_short_page() -> None:
    settings = JiraSettings(
        base_url="https://jira.example.com",
        token=SecretStr("jira_pat"),
        page_size=3,
    )
    with respx.mock(base_url="https://jira.example.com") as mock:
        route = mock.get("/rest/agile/1.0/board").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "startAt": 0,
                        "maxResults": 3,
                        "isLast": False,
                        "values": [{"id": 1}, {"id": 2}],
                    },
                ),
                httpx.Response(
                    200,
                    json={
                        "startAt": 2,
                        "maxResults": 3,
                        "isLast": True,
                        "values": [{"id": 3}],
                    },
                ),
            ]
        )
        with JiraClient(settings) as client:
            rows = list(client.list_boards())

    assert [row["id"] for row in rows] == [1, 2, 3]
    assert route.calls[1].request.url.params["startAt"] == "2"


def test_list_sprints_continues_when_is_last_false_on_short_page() -> None:
    settings = JiraSettings(
        base_url="https://jira.example.com",
        token=SecretStr("jira_pat"),
        page_size=3,
    )
    with respx.mock(base_url="https://jira.example.com") as mock:
        route = mock.get("/rest/agile/1.0/board/7/sprint").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "startAt": 0,
                        "maxResults": 3,
                        "isLast": False,
                        "values": [{"id": 20}, {"id": 21}],
                    },
                ),
                httpx.Response(
                    200,
                    json={
                        "startAt": 2,
                        "maxResults": 3,
                        "isLast": True,
                        "values": [{"id": 22}],
                    },
                ),
            ]
        )
        with JiraClient(settings) as client:
            rows = list(client.list_sprints(7))

    assert [row["id"] for row in rows] == [20, 21, 22]
    assert route.calls[1].request.url.params["startAt"] == "2"


def test_agile_list_boards_uses_agile_prefix() -> None:
    with respx.mock(base_url="https://jira.example.com") as mock:
        route = mock.get("/rest/agile/1.0/board").mock(
            return_value=httpx.Response(
                200,
                json={"startAt": 0, "maxResults": 50, "isLast": True, "values": [{"id": 7}]},
            )
        )
        with JiraClient(_settings()) as client:
            rows = list(client.list_boards(project_key_or_id="ABC", limit=10))

    assert rows == [{"id": 7}]
    assert route.calls[0].request.url.params["projectKeyOrId"] == "ABC"
